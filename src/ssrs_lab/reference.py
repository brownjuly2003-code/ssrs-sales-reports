import json
from pathlib import Path

import pandas as pd

CASES = {
    "all": {},
    "north": {"regions": [1]},
    "two_categories": {"categories": [1, 3]},
    "partial": {"start": "2026-01-08", "end": "2026-01-18", "stores": [2, 6]},
    "first_day": {"end": "2026-01-01"},
}


def totals(units, cents, stock):
    units, cents = int(units), int(cents)
    price = (2 * cents + units) // (2 * units) if units else None
    return {"Units": units, "RevenueCents": cents, "PriceCents": price, "OnHand": int(stock)}


def summarize(
    folder, start="2026-01-01", end="2026-01-31", regions=None, stores=None, categories=None
):
    if start > end:
        raise ValueError("Start date exceeds end date")

    def read(name):
        return pd.read_csv(folder / f"{name}.csv")

    sales, stock = read("Sales"), read("Stock")
    keys = ["StoreId", "ProductId"]
    values = sales.loc[sales.Day.between(start, end)].groupby(keys)[["Units", "RevenueCents"]].sum()
    balance = stock.loc[stock.Day == end].set_index(keys)["OnHand"]
    if balance.empty:
        # A missing snapshot is unknown stock, not zero stock: refuse instead of fillna(0).
        raise ValueError(f"No stock snapshot on end date {end}")
    frame = values.join(balance, how="outer").fillna(0).reset_index()
    frame = frame.merge(read("Store"), on="StoreId", validate="many_to_one")
    frame = frame.merge(read("Product"), on="ProductId", validate="many_to_one")
    for column, selected in (
        ("RegionId", regions),
        ("StoreId", stores),
        ("CategoryId", categories),
    ):
        if selected is not None:
            frame = frame.loc[frame[column].isin(selected)]
    columns = ["Units", "RevenueCents", "OnHand"]
    groups = []
    for depth in (1, 2, 3):
        levels = ["RegionId", "StoreId", "CategoryId"][:depth]
        for row in frame.groupby(levels)[columns].sum().reset_index().itertuples(index=False):
            groups.append(
                {
                    **{k: int(getattr(row, k)) for k in levels},
                    **totals(row.Units, row.RevenueCents, row.OnHand),
                }
            )
    return {"total": totals(*frame[columns].sum()), "groups": groups}


def reference(folder: Path, output: Path):
    result = {case: summarize(folder, **params) for case, params in CASES.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return result
