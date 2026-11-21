"""Recount the committed ReportViewer exports against the offline reference.

Reads only committed files under docs/runs plus the seeded dictionaries:
- offline-evidence.zip / reference.json: independent totals and group subtotals (by ids);
- 2026-11-19-render/replay.zip: the ReportRows tables passed to ReportViewer;
- 2026-11-19-render/<case>.xlsx and <case>.pdf: the exported files;
- region, store, category and product names from ssrs_lab.data.generate (seed 39).

For each case the XLSX is read as a tree: outline levels 0-2 and the row order give the
path "region > store > category" of every group row, level 3 gives the product under it.
Checked:
- the period in the title, the "Всего" row and every group row by its full name path
  against the reference subtotals (units, revenue, stock);
- every product row by its full path against the replay rows;
- the price column independently: revenue / units for every row, empty when nothing sold;
- fills: the stock cell of a product row is #FFE2C9 below 8 and white otherwise (8
  included); #FFE2C9 anywhere else fails.
PDF: all.pdf is the committed sample and must exist. Every present PDF must be one A4
page with "Страница 1 из 1", the case period, exactly the case regions and the four
printed values of each region row and of "Всего". Absent PDFs are reported as
"content not checked".

Needs openpyxl and pypdf (group dev). Run from any directory:
    python scripts/check_render.py [--render-dir DIR]
Exit code 0 means every check passed.
"""

import argparse
import csv
import json
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import openpyxl
import pypdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ssrs_lab.data import generate  # noqa: E402
from ssrs_lab.reference import CASES  # noqa: E402

RUNS = ROOT / "docs" / "runs"
RENDER = RUNS / "2026-11-19-render"
EVIDENCE = RUNS / "offline-evidence.zip"
REQUIRED_PDFS = {"all"}
LOW_STOCK = 8
LOW_STOCK_FILL = "FFE2C9"
WHITE_FILL = "FFFFFF"
LEVELS = ("RegionId", "StoreId", "CategoryId")
NAMES = ("RegionName", "StoreName", "CategoryName", "ProductName")
A4_POINTS = (595, 842)
PRICE_TOLERANCE = 1e-9


def captions():
    """Id -> name dictionaries of the seeded data, independent of the replay tables."""
    with tempfile.TemporaryDirectory() as folder:
        generate(Path(folder))
        result = {}
        for table in ("Region", "Store", "Category", "Product"):
            with (Path(folder) / f"{table}.csv").open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            result[table] = {int(r[f"{table}Id"]): r[f"{table}Name"] for r in rows}
    return result


def period(case):
    params = CASES[case]
    start = date.fromisoformat(params.get("start", "2026-01-01"))
    end = date.fromisoformat(params.get("end", "2026-01-31"))
    return f"Продажи и остатки за {start:%d.%m.%Y} — {end:%d.%m.%Y}"


def metrics(units, cents, on_hand):
    return int(units), int(cents), int(on_hand)


def expected_groups(expected, names):
    """Reference subtotals keyed by the name path region > store > category."""
    tables = ("Region", "Store", "Category")
    groups = {}
    for group in expected["groups"]:
        path = tuple(names[t][group[k]] for t, k in zip(tables, LEVELS) if k in group)
        groups[path] = metrics(group["Units"], group["RevenueCents"], group["OnHand"])
    return groups


def fill_rgb(cell):
    if cell.fill is None or cell.fill.fill_type is None:
        return None
    rgb = cell.fill.fgColor.rgb
    return rgb.upper() if isinstance(rgb, str) else None


def read_sheet(sheet, failures):
    """Group rows by name path, product rows by full path, the total row."""
    header = next(i for i in range(1, sheet.max_row + 1) if sheet.cell(i, 1).value == "Уровень")
    stack, groups, products, total = [], {}, {}, None
    for i in range(header + 1, sheet.max_row + 1):
        label, revenue, units, price, on_hand = (sheet.cell(i, col).value for col in range(1, 6))
        if label is None:
            continue
        level = sheet.row_dimensions[i].outlineLevel
        record = {
            "row": i,
            "values": metrics(units, round(revenue * 100), on_hand),
            "price": price,
        }
        label = str(label).strip()
        if label == "Всего" and level == 0:
            if total is not None:
                failures.append("xlsx has more than one total row")
            total = record
        elif level <= 2:
            if len(stack) < level:
                failures.append(f"xlsx row {i}: group level {level} without a parent")
            stack = stack[:level] + [label]
            path = tuple(stack)
            if path in groups:
                failures.append(f"xlsx group {path} appears twice")
            groups[path] = record
        elif level == 3 and len(stack) == 3:
            products[(*stack, label)] = record
        else:
            failures.append(f"xlsx row {i}: unexpected outline level {level}")
    return groups, products, total


def check_price(record, where, failures):
    units, cents, _ = record["values"]
    price = record["price"]
    if units == 0:
        if price not in (None, ""):
            failures.append(f"{where}: price {price!r} for zero units")
    elif not isinstance(price, int | float) or abs(price - cents / units / 100) > PRICE_TOLERANCE:
        failures.append(f"{where}: price {price!r} != {cents / units / 100}")


def check_fills(sheet, products, failures):
    stock_cells = {record["row"]: record["values"][2] for record in products.values()}
    low = white_at_threshold = 0
    for row in sheet.iter_rows():
        for cell in row:
            rgb = fill_rgb(cell)
            is_stock_cell = cell.column == 5 and cell.row in stock_cells
            if is_stock_cell:
                on_hand = stock_cells[cell.row]
                want = LOW_STOCK_FILL if on_hand < LOW_STOCK else WHITE_FILL
                if rgb is None or not rgb.endswith(want):
                    failures.append(f"xlsx {cell.coordinate}: stock {on_hand} has fill {rgb}")
                elif on_hand < LOW_STOCK:
                    low += 1
                elif on_hand == LOW_STOCK:
                    white_at_threshold += 1
            elif rgb is not None and rgb.endswith(LOW_STOCK_FILL):
                failures.append(f"xlsx {cell.coordinate}: low-stock fill outside a stock cell")
    return low, white_at_threshold


def money(cents):
    return f"{cents // 100:,}".replace(",", " ") + f",{cents % 100:02}"


def count(value):
    return f"{value:,}".replace(",", " ")


def check_pdf(case, path, regions, total, names, failures):
    if not path.exists():
        if case in REQUIRED_PDFS:
            failures.append(f"required sample {path.name} is missing")
            return "pdf missing"
        return "pdf content not checked (not in repository, SHA-256 in the render record)"
    pdf = pypdf.PdfReader(path)
    text = "\n".join(page.extract_text() for page in pdf.pages).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    flat = " ".join(text.split())
    box = pdf.pages[0].mediabox
    size = (round(box.width), round(box.height))
    if len(pdf.pages) != 1 or "Страница 1 из 1" not in flat:
        failures.append(f"pdf has {len(pdf.pages)} pages or no 'Страница 1 из 1'")
    if size != A4_POINTS:
        failures.append(f"pdf page size {size} is not A4 {A4_POINTS}")
    if period(case) not in flat:
        failures.append(f"pdf has no period line '{period(case)}'")
    printed = [(name, values) for (name,), values in regions.items()] + [("Всего", total)]
    for name, (units, cents, on_hand) in printed:
        rows = [i for i, line in enumerate(lines) if line == name]
        if len(rows) != 1:
            failures.append(f"pdf row '{name}' found {len(rows)} times")
            continue
        got = lines[rows[0] + 1 : rows[0] + 5]
        want_price = cents / units / 100 if units else None
        try:
            price = float(got[2].replace(" ", "").replace(",", "."))
        except (IndexError, ValueError):
            price = None
        if (
            len(got) != 4
            or [got[0], got[1], got[3]] != [money(cents), count(units), count(on_hand)]
            or price is None
            or abs(price - want_price) > 0.005 + PRICE_TOLERANCE
        ):
            failures.append(f"pdf row '{name}' prints {got}")
    unexpected = set(names["Region"].values()) - {name for (name,) in regions}
    for name in sorted(unexpected & set(lines)):
        failures.append(f"pdf prints region '{name}' outside the case")
    return f"pdf pages={len(pdf.pages)} size={size[0]}x{size[1]}pt rows={len(printed)}"


def check_case(case, expected, replay, render_dir, names):
    failures = []
    rows = json.loads(replay.read(f"{case}.json"))["Data"]["ReportRows"]
    tables = ("Region", "Store", "Category", "Product")
    ids = LEVELS + ("ProductId",)
    by_id = defaultdict(lambda: [0, 0, 0])
    want_products = {}
    for row in rows:
        if any(names[t][int(row[k])] != row[n] for t, k, n in zip(tables, ids, NAMES)):
            failures.append(f"replay names differ from dictionaries: {row}")
        values = metrics(row.get("Units") or 0, row.get("RevenueCents") or 0, row["OnHand"])
        want_products[tuple(row[n] for n in NAMES)] = values
        dims = tuple(int(row[k]) for k in LEVELS)
        for depth in range(len(LEVELS) + 1):
            acc = by_id[dims[:depth]]
            for j in range(3):
                acc[j] += values[j]
    total = expected["total"]
    want_total = metrics(total["Units"], total["RevenueCents"], total["OnHand"])
    if tuple(by_id[()]) != want_total:
        failures.append(f"replay total {by_id[()]} != reference {want_total}")
    for group in expected["groups"]:
        key = tuple(group[k] for k in LEVELS if k in group)
        if tuple(by_id[key]) != metrics(group["Units"], group["RevenueCents"], group["OnHand"]):
            failures.append(f"replay subtotal {key} differs from reference")

    sheet = openpyxl.load_workbook(render_dir / f"{case}.xlsx").active
    if sheet.cell(1, 1).value != period(case):
        failures.append(f"xlsx title {sheet.cell(1, 1).value!r} != {period(case)!r}")
    groups, products, total_row = read_sheet(sheet, failures)
    want_groups = expected_groups(expected, names)
    got_groups = {path: record["values"] for path, record in groups.items()}
    if got_groups != want_groups:
        wrong = {
            p
            for p in got_groups.keys() | want_groups.keys()
            if got_groups.get(p) != want_groups.get(p)
        }
        failures.append(f"xlsx group rows differ from reference by path: {sorted(wrong)[:4]}")
    got_products = {path: record["values"] for path, record in products.items()}
    if got_products != want_products:
        wrong = {
            p
            for p in got_products.keys() | want_products.keys()
            if got_products.get(p) != want_products.get(p)
        }
        failures.append(f"xlsx product rows differ from replay by path: {sorted(wrong)[:4]}")
    if total_row is None:
        failures.append("xlsx has no total row")
    elif total_row["values"] != want_total:
        failures.append(f"xlsx total {total_row['values']} != reference {want_total}")
    records = [*groups.items(), *products.items()]
    if total_row is not None:
        records.append((("Всего",), total_row))
    for path, record in records:
        check_price(record, f"xlsx row {record['row']} {' > '.join(path)}", failures)
    low, white_at_threshold = check_fills(sheet, products, failures)

    regions = {path: values for path, values in want_groups.items() if len(path) == 1}
    pdf_note = check_pdf(case, render_dir / f"{case}.pdf", regions, want_total, names, failures)

    at_threshold = sum(1 for values in want_products.values() if values[2] == LOW_STOCK)
    below = sum(1 for values in want_products.values() if values[2] < LOW_STOCK)
    print(
        f"{case}: total {list(want_total)} groups={len(groups)}/{len(want_groups)} "
        f"products={len(products)}/{len(want_products)} prices={len(records)} "
        f"stock<8 filled={low}/{below} stock==8 white={white_at_threshold}/{at_threshold} "
        f"{pdf_note} -> {'FAIL' if failures else 'ok'}"
    )
    for failure in failures:
        print(f"  {failure}")
    return not failures


def run(render_dir=RENDER, evidence=EVIDENCE):
    with zipfile.ZipFile(evidence) as archive:
        reference = json.loads(archive.read("reference.json"))
    names = captions()
    with zipfile.ZipFile(render_dir / "replay.zip") as replay:
        results = [
            check_case(case, reference[case], replay, render_dir, names) for case in reference
        ]
    passed = all(results)
    print(f"{sum(results)}/{len(results)} cases passed: {'PASS' if passed else 'FAIL'}")
    return passed


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--render-dir", type=Path, default=RENDER)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args(argv)
    return 0 if run(args.render_dir, args.evidence) else 1


if __name__ == "__main__":
    raise SystemExit(main())
