"""Control DAX query for the PBIP matrix measures and its Python reference values.

docs/check-measures.dax is generated here (``python -m ssrs_lab write-dax-check``). Every row
sets the filter context of one matrix cell or total: the period and filters of one of the five
README selections (reference.CASES) plus, for subtotals, a region, store and category. The
reference values are the archived independent pandas result (reference.summarize) in
docs/runs/offline-evidence.zip: units, revenue in cents and closing stock of the total and of
all 143 group rows; the average price is revenue / units without rounding (the report rounds
it to kopecks, the measure does not). Two product cells of the 1 January selection come from
pandas over the generated CSVs (product_cells): one with sales and one with stock but no sales,
where the matrix must show 0 sold, 0 revenue, a blank price and the stock.

The query is evaluated outside CI by scripts/excel_dax_check.py in the Excel Data Model
(Windows, Excel, pywin32). In Power BI Desktop the same query runs against the measures of
the loaded model.
"""

import hashlib
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

from .reference import CASES

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "powerbi" / "Retail.SemanticModel" / "definition"
ARCHIVE = ROOT / "docs" / "runs" / "offline-evidence.zip"
DATA = ROOT / "data"
QUERY = ROOT / "docs" / "check-measures.dax"
MEASURE_RE = re.compile(r"^\tmeasure (.+?) = (.+)$", re.M)
ROW_RE = re.compile(r'^\s+\( "([^"]+)", "([^"]+)", (BLANK \(\)|-?[\d.e+-]+), (.+) \),?$', re.M)
TOLERANCE = "0.0000001"
MEASURES = ["Units Sold", "Revenue", "Average Price", "Closing Stock"]
LEVELS = [("RegionId", "R", "'Store'[RegionId]"), ("StoreId", "S", "'Store'[StoreId]")]
LEVELS.append(("CategoryId", "C", "'Product'[CategoryId]"))


def measure_definitions(model=MODEL):
    """[(table, measure, formula)] read from the TMDL files, in file order."""
    found = []
    for path in sorted((Path(model) / "tables").glob("*.tmdl")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        for name, formula in MEASURE_RE.findall(text):
            found.append((path.stem, name.strip("'"), formula))
    return found


def define_block(model=MODEL):
    lines = [f"MEASURE '{t}'[{n}] = {f}" for t, n, f in measure_definitions(model)]
    return "DEFINE\n" + "\n".join(lines) + "\n"


def text_sha256(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def archived_reference(archive=ARCHIVE):
    with zipfile.ZipFile(archive) as bundle:
        return json.loads(bundle.read("reference.json"))


def _date(value):
    y, m, d = (int(part) for part in value.split("-"))
    return f"DATE ( {y}, {m}, {d} )"


def case_filters(params):
    """DAX filters of one README selection (the same defaults as reference.summarize)."""
    start, end = params.get("start", "2026-01-01"), params.get("end", "2026-01-31")
    filters = [f"DATESBETWEEN ( 'Calendar'[Day], {_date(start)}, {_date(end)} )"]
    for key, column in [
        ("regions", "'Store'[RegionId]"),
        ("stores", "'Store'[StoreId]"),
        ("categories", "'Product'[CategoryId]"),
    ]:
        if params.get(key) is not None:
            values = ", ".join(str(v) for v in params[key])
            filters.append(f"{column} IN {{ {values} }}")
    return filters


def product_cells(data=DATA):
    """Two product rows of the first_day matrix: (store, product, units, cents, stock)."""
    day = "2026-01-01"
    sales = pd.read_csv(Path(data) / "Sales.csv")
    stock = pd.read_csv(Path(data) / "Stock.csv")
    sold = sales[sales.Day == day].groupby(["StoreId", "ProductId"])[["Units", "RevenueCents"]]
    sold = sold.sum()
    snapshot = stock[stock.Day == day].set_index(["StoreId", "ProductId"])["OnHand"]
    unsold = sorted(k for k, v in snapshot.items() if v > 0 and k not in sold.index)
    cells = []
    for key in [(1, 1), unsold[0]]:
        units, cents = (int(sold.loc[key, c]) if key in sold.index else 0 for c in sold.columns)
        cells.append((*key, units, cents, int(snapshot[key])))
    return cells


def control_rows(archive=ARCHIVE, data=DATA):
    """[(measure, context, [DAX filters], reference value)] for every total and group row
    and two product cells."""
    reference = archived_reference(archive)
    rows = []
    for case, params in CASES.items():
        base = case_filters(params)
        cells = [(case, [], reference[case]["total"])]
        for group in reference[case]["groups"]:
            parts, filters = [case], []
            for key, short, column in LEVELS:
                if key in group:
                    parts.append(f"{short}{group[key]}")
                    filters.append(f"{column} = {group[key]}")
            cells.append((" / ".join(parts), filters, group))
        for context, filters, values in cells:
            units, cents = values["Units"], values["RevenueCents"]
            expected = {
                "Units Sold": units,
                "Revenue": cents / 100,
                "Average Price": cents / 100 / units if units else None,
                "Closing Stock": values["OnHand"],
            }
            for measure in MEASURES:
                rows.append((measure, context, base + filters, expected[measure]))
    base = case_filters(CASES["first_day"])
    for store, product, units, cents, stock in product_cells(data):
        filters = [f"'Store'[StoreId] = {store}", f"'Product'[ProductId] = {product}"]
        expected = {
            "Units Sold": units,
            "Revenue": cents / 100,
            "Average Price": cents / 100 / units if units else None,
            "Closing Stock": stock,
        }
        for measure in MEASURES:
            context = f"first_day / S{store} / P{product}"
            rows.append((measure, context, base + filters, expected[measure]))
    return rows


def _literal(value):
    if value is None:
        return "BLANK ()"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def build_query(root=ROOT, data=None):
    archive = Path(root) / ARCHIVE.relative_to(ROOT)
    data = Path(data) if data else Path(root) / "data"
    lines = []
    for measure, context, filters, value in control_rows(archive, data):
        actual = f"CALCULATE ( [{measure}], {', '.join(filters)} )"
        lines.append(f'        ( "{measure}", "{context}", {_literal(value)}, {actual} ),')
    lines[-1] = lines[-1].rstrip(",")
    passed = (
        "IF ( ISBLANK ( [Value3] ), ISBLANK ( [Value4] ), NOT ISBLANK ( [Value4] ) && "
        f"ABS ( [Value4] - [Value3] ) <= {TOLERANCE} * MAX ( 1, ABS ( [Value3] ) ) )"
    )
    return "\n".join(
        [
            "// Generated by `python -m ssrs_lab write-dax-check` (dax_check.py).",
            "// Expected: reference.json of docs/runs/offline-evidence.zip (pandas over the CSVs);",
            "// Average Price = revenue / units, not rounded. Context: selection from README",
            "// (all, north, two_categories, partial, first_day) / region R / store S / category C;",
            "// two product cells (S / P) of 1 January: pandas over the generated CSVs.",
            "EVALUATE",
            "VAR ReferenceRows =",
            "    {",
            *lines,
            "    }",
            "RETURN",
            "    SELECTCOLUMNS (",
            "        ReferenceRows,",
            '        "Measure", [Value1],',
            '        "Context", [Value2],',
            '        "Expected", [Value3],',
            '        "Actual", [Value4],',
            f'        "Result", IF ( {passed}, "PASS", "FAIL" )',
            "    )",
            "ORDER BY [Measure], [Context]",
            "",
        ]
    )


def parse_query(text):
    """[(measure, context, expected or None, actual expression)] from the query text."""
    rows = []
    for measure, context, value, actual in ROW_RE.findall(text):
        rows.append((measure, context, None if value == "BLANK ()" else float(value), actual))
    return rows


def write_query(root=ROOT):
    path = Path(root) / QUERY.relative_to(ROOT)
    path.write_bytes(build_query(root).encode("utf-8"))
    return {"written": path.as_posix(), "rows": len(control_rows(data=Path(root) / "data"))}
