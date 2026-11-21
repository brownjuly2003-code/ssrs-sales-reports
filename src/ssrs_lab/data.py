import csv
from datetime import date, timedelta
from pathlib import Path
from random import Random

HEADERS = {
    "Region": ["RegionId", "RegionName"],
    "Store": ["StoreId", "RegionId", "StoreName"],
    "Category": ["CategoryId", "CategoryName"],
    "Product": ["ProductId", "CategoryId", "ProductName"],
    "Calendar": ["Day"],
    "Sales": ["Day", "StoreId", "ProductId", "Units", "RevenueCents"],
    "Stock": ["Day", "StoreId", "ProductId", "OnHand"],
}


def generate(folder: Path, seed: int = 39):
    folder.mkdir(parents=True, exist_ok=True)
    rng = Random(seed)
    regions = [(1, "Север"), (2, "Юг")]
    categories = [(1, "Напитки"), (2, "Бакалея"), (3, "Фрукты"), (4, "Овощи")]
    stores = [(i, 1 + (i - 1) // 4, f"Магазин {i:02}") for i in range(1, 9)]
    products = [(i, 1 + (i - 1) % 4, f"Товар {i:02}") for i in range(1, 65)]
    days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(31)]

    def write(table, columns, rows):
        with (folder / f"{table}.csv").open("w", encoding="utf-8", newline="") as stream:
            out = csv.writer(stream, lineterminator="\n")
            out.writerow(columns)
            out.writerows(rows)

    for table, rows in (
        ("Region", regions),
        ("Store", stores),
        ("Category", categories),
        ("Product", products),
        ("Calendar", [(day,) for day in days]),
    ):
        write(table, HEADERS[table], rows)
    for table, rows, names, label in (
        ("Store", stores, dict(regions), "RegionName"),
        ("Product", products, dict(categories), "CategoryName"),
    ):
        write(f"Dim{table}", HEADERS[table] + [label], [(*r, names[r[1]]) for r in rows])

    sales, stock = [], []
    balances = {(s, p): rng.randint(1, 45) for s, *_ in stores for p, *_ in products}
    for day in days:
        for (store, product), opening in balances.items():
            available = opening + (rng.randint(12, 36) if opening < 8 else 0)
            units = min(available, rng.randint(0, 7))
            if units:
                price = 4500 + 73 * product + rng.randrange(0, 6) * 20
                sales.append((day, store, product, units, units * price))
            balances[store, product] = available - units
            stock.append((day, store, product, available - units))
    write("Sales", HEADERS["Sales"], sales)
    write("Stock", HEADERS["Stock"], stock)
