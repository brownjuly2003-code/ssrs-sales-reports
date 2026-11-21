"""Acceptance on a real SQL Server, explicitly selected with -m slow.

By default sqlcmd runs in the Compose service (Linux). RETAIL_SQLCMD (a JSON list: the sqlcmd
command with its connection options) and RETAIL_SQL_DIR (the folder of the three scripts as
the server sees them) point the same tests at another instance, e.g. the Windows runner.
"""

import csv
import json
import os
import subprocess
from pathlib import Path

import pytest

from ssrs_lab.reference import CASES, summarize, totals

pytestmark = pytest.mark.slow


COMPOSE_SQLCMD = [
    "docker",
    "compose",
    "exec",
    "-T",
    "mssql",
    "/opt/mssql-tools18/bin/sqlcmd",
    "-C",
    "-S",
    "tcp:localhost,1433",
    "-U",
    "sa",
]
SQLCMD = (
    json.loads(os.environ["RETAIL_SQLCMD"]) if "RETAIL_SQLCMD" in os.environ else COMPOSE_SQLCMD
)
SQL_DIR = os.environ.get("RETAIL_SQL_DIR", "/lab/sql")


def sql(*args, database="RetailLab"):
    command = [
        *SQLCMD,
        "-b",
        "-r",
        "1",
        "-h",
        "-1",
        "-W",
        "-s",
        "|",
        "-d",
        database,
        *args,
    ]
    return subprocess.check_output(command, text=True, encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def database():
    sql(
        "-Q",
        "IF DB_ID('RetailLab') IS NULL CREATE DATABASE RetailLab COLLATE Latin1_General_100_CI_AS_SC_UTF8",
        database="master",
    )
    for path in sorted(Path("sql").glob("[0-9][0-9]-*.sql")):
        sql("-i", f"{SQL_DIR}/{path.name}")


def ids(value):
    return "NULL" if value is None else "'" + ",".join(str(int(x)) for x in value) + "'"


@pytest.mark.parametrize("case", CASES)
def test_procedure_matches_reference(case):
    args = CASES[case]
    start, end = args.get("start", "2026-01-01"), args.get("end", "2026-01-31")
    filters = [ids(args.get(k)) for k in ("regions", "stores", "categories")]
    query = f"""
    SET NOCOUNT ON;
    CREATE TABLE #detail (RegionId int, RegionName nvarchar(80), StoreId int, StoreName nvarchar(80),
        CategoryId int, CategoryName nvarchar(80), ProductId int, ProductName nvarchar(80),
        Units int, RevenueCents bigint, OnHand int);
    INSERT INTO #detail EXEC dbo.ReportRows '{start}','{end}',{",".join(filters)};
    SELECT RegionId, StoreId, CategoryId, SUM(Units), SUM(RevenueCents), SUM(OnHand)
    FROM #detail GROUP BY GROUPING SETS ((), (RegionId), (RegionId, StoreId), (RegionId, StoreId, CategoryId));
    """
    expected = summarize(Path("data"), **args)
    actual = []
    for line in sql("-Q", query).splitlines():
        if line.strip():
            *keys, units, cents, balance = line.strip().split("|")
            dims = {
                k: int(v)
                for k, v in zip(("RegionId", "StoreId", "CategoryId"), keys)
                if v != "NULL"
            }
            actual.append({**dims, **totals(units, cents, balance)})
    wanted = [expected["total"], *expected["groups"]]
    assert sorted(map(json.dumps, actual)) == sorted(map(json.dumps, wanted))


def test_standalone_closing_snapshot():
    query = """SET NOCOUNT ON; CREATE TABLE #balance (StoreId int,ProductId int,OnHand int);
    INSERT INTO #balance EXEC dbo.StockAtDate '2026-01-18','2,6','1,3';
    SELECT SUM(OnHand) FROM #balance;"""
    expected = summarize(Path("data"), end="2026-01-18", stores=[2, 6], categories=[1, 3])
    assert int(sql("-Q", query).strip()) == expected["total"]["OnHand"]


def test_end_date_without_snapshot_returns_no_rows():
    output = sql("-Q", "SET NOCOUNT ON; EXEC dbo.SnapshotDates;")
    days = [line.strip() for line in output.splitlines() if line.strip()]
    assert days == [f"2026-01-{day:02}" for day in range(1, 32)]
    query = "SET NOCOUNT ON; EXEC dbo.ReportRows '20260101','{}',NULL,NULL,NULL;"
    closing = [line.split("|") for line in sql("-Q", query.format("20260131")).splitlines()]
    assert sum(int(row[10]) for row in closing if len(row) == 11) == 8727
    assert not sql("-Q", query.format("20260201")).strip()


@pytest.mark.parametrize(
    ("table", "procedure", "region_ids"),
    [
        ("Region", "Regions", None),
        ("Category", "Categories", None),
        ("Store", "Stores", [1]),
        ("Store", "Stores", [1, 2]),
    ],
)
def test_parameter_choices_match_csv(table, procedure, region_ids):
    with Path(f"data/{table}.csv").open(encoding="utf-8", newline="") as stream:
        wanted = [
            (row[f"{table}Id"], row[f"{table}Name"])
            for row in csv.DictReader(stream)
            if region_ids is None or int(row["RegionId"]) in region_ids
        ]
    args = "" if region_ids is None else f" @RegionIds={ids(region_ids)}"
    output = sql("-Q", f"SET NOCOUNT ON; EXEC dbo.{procedure}{args};")
    actual = [tuple(line.split("|")) for line in output.splitlines() if line.strip()]
    assert actual == sorted(wanted, key=lambda row: row[1])


def test_product_captions_in_report_rows():
    with Path("data/Product.csv").open(encoding="utf-8", newline="") as stream:
        expected = sorted((row["ProductId"], row["ProductName"]) for row in csv.DictReader(stream))
    output = sql("-Q", "SET NOCOUNT ON; EXEC dbo.ReportRows '20260101','20260101',NULL,'1',NULL;")
    captions = [tuple(line.split("|")[6:8]) for line in output.splitlines() if line.strip()]
    assert sorted(captions) == expected
