"""Offline checks of the control DAX query and the saved Excel Data Model results.

Excel is not started here: the results file comes from scripts/excel_dax_check.py on Windows.
"""

import csv
import json
import math
import re
from pathlib import Path

import pytest

from ssrs_lab import dax_check
from ssrs_lab.data import generate
from ssrs_lab.pbip import MEASURES
from ssrs_lab.reference import CASES

ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / "docs" / "check-measures.dax"
RESULTS = ROOT / "docs" / "runs" / "2026-11-21-dax-results.csv"
RUN = ROOT / "docs" / "runs" / "2026-11-21-dax-run.json"


def _rows():
    return dax_check.parse_query(QUERY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    """The seeded CSVs (seed 39): the same files as the archive manifest."""
    folder = tmp_path_factory.mktemp("data")
    generate(folder)
    return folder


def test_query_file_equals_generator(data):
    assert QUERY.read_text(encoding="utf-8") == dax_check.build_query(ROOT, data)
    assert len(_rows()) == len(dax_check.control_rows(data=data)) == 150 * 4


def test_product_cells_cover_a_sold_and_an_unsold_product(data):
    rows = {(m, c): v for m, c, v, _ in _rows()}
    (s1, p1, units, cents, stock), (s2, p2, *unsold) = dax_check.product_cells(data)
    assert (s1, p1, units, cents, stock) == (1, 1, 4, 18532, 10)
    assert rows["Average Price", "first_day / S1 / P1"] == 46.33
    assert unsold[:2] == [0, 0] and unsold[2] > 0
    sold = f"first_day / S{s2} / P{p2}"
    assert rows["Units Sold", sold] == 0 and rows["Revenue", sold] == 0
    assert rows["Average Price", sold] is None and rows["Closing Stock", sold] == unsold[2]


def test_tmdl_formulas_equal_generator_and_define_block():
    found = dax_check.measure_definitions()
    expected = [(t, n, f) for t, items in MEASURES.items() for n, f, _, _ in items]
    assert sorted(found) == sorted(expected)
    block = dax_check.define_block()
    for table, name, formula in expected:
        assert f"MEASURE '{table}'[{name}] = {formula}\n" in block


def test_every_measure_is_covered_in_every_cell():
    names = {name for _, name, _ in dax_check.measure_definitions()}
    assert names == set(dax_check.MEASURES)
    cells = {}
    for measure, context, _, actual in _rows():
        assert actual.startswith(f"CALCULATE ( [{measure}], DATESBETWEEN ( 'Calendar'[Day], ")
        cells.setdefault(context, set()).add(measure)
    assert all(found == names for found in cells.values())
    assert len(cells) == 150


def test_reference_values_come_from_the_archived_pandas_result():
    reference = dax_check.archived_reference()
    rows = {(m, c): v for m, c, v, _ in _rows()}
    for case in CASES:
        total = reference[case]["total"]
        assert rows["Units Sold", case] == total["Units"]
        assert math.isclose(rows["Revenue", case], total["RevenueCents"] / 100, rel_tol=1e-15)
        assert rows["Closing Stock", case] == total["OnHand"]
        price = rows["Average Price", case]
        # The report rounds the price to kopecks; the measure does not.
        assert abs(price * 100 - total["PriceCents"]) <= 0.5 + 1e-9
        assert price == total["RevenueCents"] / 100 / total["Units"]
    assert sum(len(reference[case]["groups"]) + 1 for case in CASES) == 148


def test_readme_matrix_rows_are_in_the_query():
    rows = {(m, c): v for m, c, v, _ in _rows()}
    assert rows["Units Sold", "all"] == 55523
    assert rows["Revenue", "all"] == 3840145.58
    assert rows["Closing Stock", "all"] == 8727
    assert rows["Units Sold", "partial"] == 5019 and rows["Closing Stock", "first_day"] == 12022


def test_case_filters_follow_the_reference_selections():
    rows = {(m, c): a for m, c, _, a in _rows()}
    partial = rows["Units Sold", "partial"]
    assert "DATE ( 2026, 1, 8 ), DATE ( 2026, 1, 18 )" in partial
    assert "'Store'[StoreId] IN { 2, 6 }" in partial
    assert "'Store'[RegionId] IN { 1 }" in rows["Revenue", "north"]
    assert "'Product'[CategoryId] IN { 1, 3 }" in rows["Revenue", "two_categories"]
    assert "DATE ( 2026, 1, 1 ), DATE ( 2026, 1, 1 )" in rows["Closing Stock", "first_day"]
    group = rows["Closing Stock", "all / R2 / S5 / C4"]
    assert re.search(
        r"'Store'\[RegionId\] = 2, 'Store'\[StoreId\] = 5, 'Product'\[CategoryId\] = 4", group
    )


def test_saved_results_are_all_pass_for_the_query_rows():
    with RESULTS.open(encoding="utf-8", newline="") as handle:
        saved = list(csv.DictReader(handle))
    rows = {(m, c): v for m, c, v, _ in _rows()}
    assert {(r["measure"], r["context"]) for r in saved} == set(rows)
    assert len(saved) == len(rows)
    for row in saved:
        assert row["result"] == "PASS", row
        expected = rows[row["measure"], row["context"]]
        if expected is None:
            assert row["expected"] == row["actual"] == ""
            continue
        assert math.isclose(float(row["expected"]), expected, rel_tol=1e-12)
        actual = float(row["actual"])
        assert abs(actual - expected) <= 1e-7 * max(1, abs(expected))


def test_saved_run_used_the_current_formulas_and_query():
    run = json.loads(RUN.read_text(encoding="utf-8"))
    assert run["define_sha256"] == dax_check.text_sha256(dax_check.define_block())
    assert run["query_sha256"] == dax_check.text_sha256(QUERY.read_text(encoding="utf-8"))
    assert run["measures"] == 4 and run["control_rows"] == run["passed"] == 600
    assert run["failed"] == []
    assert sorted(run["negative_control_failed"]) == sorted(run["negative_control_corrupted"])
    assert len(run["negative_control_failed"]) == 2
