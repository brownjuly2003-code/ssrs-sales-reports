"""Independently recompute every group from CSV and compare RDL with SQL projections."""

import argparse
import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

from sqlglot import parse_one

CASES = {
    "all": (1, 31, (), (), ()),
    "north": (1, 31, (1,), (), ()),
    "two_categories": (1, 31, (), (), (1, 3)),
    "partial": (8, 18, (), (2, 6), ()),
    "first_day": (1, 1, (), (), ()),
}


def read(folder, name):
    with (folder / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def audit(data, report):
    expected = json.loads(report.read_bytes())
    if expected.keys() != CASES.keys():
        raise ValueError("Scenario set differs")
    store = {int(r["StoreId"]): int(r["RegionId"]) for r in read(data, "Store")}
    product = {int(r["ProductId"]): int(r["CategoryId"]) for r in read(data, "Product")}
    sales, stock = read(data, "Sales"), read(data, "Stock")
    checked = 0
    for case, (start, end, regions, stores, categories) in CASES.items():
        lower, upper = f"2026-01-{start:02}", f"2026-01-{end:02}"
        grouped = defaultdict(lambda: [0, 0, 0])
        for rows, is_stock in ((sales, False), (stock, True)):
            for r in rows:
                day = r["Day"]
                if (is_stock and day != upper) or not lower <= day <= upper:
                    continue
                shop, cat = int(r["StoreId"]), product[int(r["ProductId"])]
                region = store[shop]
                if (
                    (stores and shop not in stores)
                    or (categories and cat not in categories)
                    or (regions and region not in regions)
                ):
                    continue
                dims = (region, shop, cat)
                for level in range(4):
                    value = grouped[dims[:level]]
                    if is_stock:
                        value[2] += int(r["OnHand"])
                    else:
                        value[0] += int(r["Units"])
                        value[1] += int(r["RevenueCents"])
        targets = [expected[case]["total"], *expected[case]["groups"]]
        if len(targets) != len(grouped):
            raise ValueError(f"{case}: group count")
        for target in targets:
            key = tuple(target[k] for k in ("RegionId", "StoreId", "CategoryId") if k in target)
            units, cents, onhand = grouped.pop(key)
            quotient, remainder = divmod(cents, units) if units else (0, 0)
            price = quotient + int(2 * remainder >= units) if units else None
            if [units, cents, price, onhand] != [
                target[k] for k in ("Units", "RevenueCents", "PriceCents", "OnHand")
            ]:
                raise ValueError(f"{case}: mismatch for {key}")
            checked += 1
    sql = Path("sql/03-datasets.sql").read_text(encoding="utf-8")
    for ds in ET.parse("rdl/SalesStock.rdl").findall(".//{*}DataSet"):
        proc = ds.findtext("{*}Query/{*}CommandText")
        batch = next(
            b
            for b in re.split(r"(?im)^GO\s*$", sql)
            if re.search(r"PROCEDURE\s+" + re.escape(proc) + r"\b", b)
        )
        query = re.split(r"(?im)^AS\s*$", batch, maxsplit=1)[1]
        columns = parse_one(query, read="tsql").named_selects
        if set(columns) != {e.text for e in ds.findall("{*}Fields/{*}Field/{*}DataField")}:
            raise ValueError(f"Projection mismatch: {proc}")
    return {
        "scenarios": len(CASES),
        "group_totals_checked": checked,
        "sales_rows": len(sales),
        "stock_rows": len(stock),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--reference", type=Path, default=Path("reports/reference.json"))
    archive = parser.add_mutually_exclusive_group()
    archive.add_argument("--write-archive", type=Path)
    archive.add_argument("--verify-archive", type=Path)
    args = parser.parse_args()
    result = audit(args.data, args.reference)
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.data.glob("*.csv"))
    }
    files["reference.json"] = hashlib.sha256(args.reference.read_bytes()).hexdigest()
    manifest = {"seed": 39, "sha256": files, "audit": result}
    if args.write_archive:
        args.write_archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.write_archive, "w", zipfile.ZIP_DEFLATED) as arc:
            arc.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            arc.write(args.reference, "reference.json")
    if args.verify_archive:
        with zipfile.ZipFile(args.verify_archive) as arc:
            if (
                json.loads(arc.read("manifest.json")) != manifest
                or arc.read("reference.json") != args.reference.read_bytes()
            ):
                raise ValueError("Golden archive differs from regenerated data")
    print("PASS", json.dumps(result))


if __name__ == "__main__":
    main()
