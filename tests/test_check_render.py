"""Corrupted copies of the committed exports must fail scripts/check_render.py.

SSRS_RENDER_DIR points the same checks at another folder of exports with replay.zip, e.g.
the files rendered by the report server on the Windows runner.
"""

import importlib.util
import os
import shutil
from pathlib import Path

import openpyxl
import pytest
from openpyxl.styles import PatternFill

ROOT = Path(__file__).resolve().parents[1]
RENDER = Path(os.environ.get("SSRS_RENDER_DIR", ROOT / "docs" / "runs" / "2026-11-19-render"))
ORANGE = PatternFill("solid", fgColor="FFFFE2C9")
BLUE = PatternFill("solid", fgColor="FF9BC2E6")
WHITE = PatternFill("solid", fgColor="FFFFFFFF")


def load_script():
    spec = importlib.util.spec_from_file_location("check_render", ROOT / "scripts/check_render.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_render = load_script()


@pytest.fixture
def exports(tmp_path):
    target = tmp_path / "render"
    shutil.copytree(RENDER, target)
    return target


def edit(path, change):
    book = openpyxl.load_workbook(path)
    change(book.active)
    book.save(path)


def product_rows(sheet, condition):
    return [
        i
        for i in range(1, sheet.max_row + 1)
        if sheet.row_dimensions[i].outlineLevel == 3 and condition(sheet, i)
    ]


def row_of(sheet, label):
    return next(i for i in range(1, sheet.max_row + 1) if sheet.cell(i, 1).value == label)


def test_committed_and_resaved_exports_pass(exports):
    assert check_render.run(exports)
    edit(exports / "first_day.xlsx", lambda sheet: None)
    assert check_render.run(exports)


def test_swapped_region_names_fail(exports):
    def swap(sheet):
        north, south = row_of(sheet, "Север"), row_of(sheet, "Юг")
        sheet.cell(north, 1).value, sheet.cell(south, 1).value = "Юг", "Север"

    edit(exports / "first_day.xlsx", swap)
    assert not check_render.run(exports)


def test_swapped_product_names_fail(exports):
    def swap(sheet):
        first, second = product_rows(sheet, lambda s, i: True)[:2]
        a, b = sheet.cell(first, 1), sheet.cell(second, 1)
        a.value, b.value = b.value, a.value

    edit(exports / "partial.xlsx", swap)
    assert not check_render.run(exports)


def test_low_stock_fill_outside_stock_cell_fails(exports):
    def move(sheet):
        low = product_rows(sheet, lambda s, i: s.cell(i, 5).value < 8)[0]
        unsold = product_rows(sheet, lambda s, i: s.cell(i, 3).value == 0)[0]
        sheet.cell(low, 5).fill = WHITE
        sheet.cell(unsold, 3).fill = ORANGE

    edit(exports / "first_day.xlsx", move)
    assert not check_render.run(exports)


def test_stock_at_threshold_must_stay_white(exports):
    def paint(sheet):
        at_eight = product_rows(sheet, lambda s, i: s.cell(i, 5).value == 8)[0]
        sheet.cell(at_eight, 5).fill = BLUE

    edit(exports / "first_day.xlsx", paint)
    assert not check_render.run(exports)


def test_group_row_with_low_stock_fill_fails(exports):
    edit(
        exports / "north.xlsx",
        lambda sheet: setattr(sheet.cell(row_of(sheet, "Север"), 5), "fill", ORANGE),
    )
    assert not check_render.run(exports)


def test_wrong_price_fails(exports):
    def shift(sheet):
        sold = product_rows(sheet, lambda s, i: s.cell(i, 3).value)[0]
        sheet.cell(sold, 4).value += 0.01

    edit(exports / "two_categories.xlsx", shift)
    assert not check_render.run(exports)


def test_price_for_unsold_product_must_be_empty(exports):
    def fill_price(sheet):
        unsold = product_rows(sheet, lambda s, i: s.cell(i, 3).value == 0)[0]
        sheet.cell(unsold, 4).value = 0

    edit(exports / "first_day.xlsx", fill_price)
    assert not check_render.run(exports)


def test_foreign_pdf_fails(exports):
    shutil.copy(exports / "all.pdf", exports / "partial.pdf")
    assert not check_render.run(exports)


def test_missing_sample_pdf_fails(exports):
    (exports / "all.pdf").unlink()
    assert not check_render.run(exports)
