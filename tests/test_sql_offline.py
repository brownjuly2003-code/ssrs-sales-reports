"""Execute translated dataset SELECTs on CSV. The native T-SQL gate is test_mssql."""

import re
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest
from sqlglot import parse_one

from ssrs_lab.data import HEADERS, generate
from ssrs_lab.reference import CASES, summarize, totals


@pytest.fixture(scope="module")
def report_sql(tmp_path_factory):
    folder = tmp_path_factory.mktemp("sql_csv")
    generate(folder)
    with duckdb.connect(config={"threads": "1", "memory_limit": "96MB"}) as db:
        db.execute("CREATE SCHEMA dbo")
        for table in HEADERS:
            db.read_csv(str(folder / f"{table}.csv"), header=True).create(f"dbo.{table}")
        db.execute("""CREATE MACRO string_split(input, separator) AS TABLE
                      SELECT unnest(string_to_array(input, separator)) AS value""")
        procedures = {}
        for batch in re.split(
            r"(?im)^GO\s*$", Path("sql/03-datasets.sql").read_text(encoding="utf-8")
        ):
            if not batch.strip():
                continue
            name = re.search(r"PROCEDURE dbo\.(\w+)", batch)[1]
            body = re.split(r"(?m)^AS\s*$", batch, maxsplit=1)[1]
            procedures[name] = parse_one(body, read="tsql").sql(dialect="duckdb")
        yield db, procedures, folder


@pytest.mark.parametrize("case", CASES)
def test_select_returns_expected_group_values(report_sql, case):
    db, procedures, folder = report_sql
    filters = CASES[case]
    args = {
        "StartDate": date.fromisoformat(filters.get("start", "2026-01-01")),
        "EndDate": date.fromisoformat(filters.get("end", "2026-01-31")),
    }
    for param, key in (
        ("RegionIds", "regions"),
        ("StoreIds", "stores"),
        ("CategoryIds", "categories"),
    ):
        args[param] = ",".join(map(str, filters[key])) if key in filters else None
    result = db.execute(procedures["ReportRows"], args).fetchdf()
    expected = summarize(folder, **filters)
    for target in [expected["total"], *expected["groups"]]:
        frame = result
        for k in ("RegionId", "StoreId", "CategoryId"):
            if k in target:
                frame = frame[frame[k] == target[k]]
        metrics = totals(frame["Units"].sum(), frame["RevenueCents"].sum(), frame["OnHand"].sum())
        assert all(target[k] == v for k, v in metrics.items())


def test_cascade_and_end_date(report_sql):
    db, procedures, folder = report_sql
    assert db.execute(procedures["Regions"]).fetchall() == [(1, "Север"), (2, "Юг")]
    assert len(db.execute(procedures["Categories"]).fetchall()) == 4
    assert [r[0] for r in db.execute(procedures["Stores"], {"RegionIds": "2"}).fetchall()] == [
        5,
        6,
        7,
        8,
    ]
    args = {"EndDate": date(2026, 1, 18), "StoreIds": "2,6", "CategoryIds": "1,3"}
    rows = db.execute(procedures["StockAtDate"], args).fetchall()
    assert (
        sum(r[2] for r in rows)
        == summarize(folder, end="2026-01-18", stores=[2, 6], categories=[1, 3])["total"]["OnHand"]
    )


def test_end_date_without_snapshot_returns_no_rows(report_sql):
    db, procedures, folder = report_sql
    days = [r[0] for r in db.execute(procedures["SnapshotDates"]).fetchall()]
    assert days == [date(2026, 1, 1) + timedelta(days=i) for i in range(31)]
    args = {"StartDate": date(2026, 1, 1), "RegionIds": None, "StoreIds": None, "CategoryIds": None}
    closing = db.execute(procedures["ReportRows"], {**args, "EndDate": date(2026, 1, 31)})
    assert sum(row[10] for row in closing.fetchall()) == 8727
    missing = db.execute(procedures["ReportRows"], {**args, "EndDate": date(2026, 2, 1)})
    assert missing.fetchall() == []
    with pytest.raises(ValueError, match="snapshot"):
        summarize(folder, end="2026-02-01")
