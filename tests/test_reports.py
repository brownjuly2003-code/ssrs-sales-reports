import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_report_has_hierarchical_groups_and_multiselect():
    report = ET.parse(ROOT / "rdl/SalesStock.rdl")
    groups = [e.attrib["Name"] for e in report.findall(".//{*}TablixRowHierarchy//{*}Group")]
    assert groups == ["ByRegion", "ByStore", "ByCategory", "ByProduct"]
    params = {
        p.attrib["Name"]
        for p in report.findall(".//{*}ReportParameter")
        if p.findtext("{*}MultiValue") == "true"
    }
    assert params == {"RegionIds", "StoreIds", "CategoryIds"}
    assert report.findtext(".//{*}Language") == "ru-RU"


def test_end_date_offers_only_snapshot_days():
    report = ET.parse(ROOT / "rdl/SalesStock.rdl")
    end = next(p for p in report.findall(".//{*}ReportParameter") if p.get("Name") == "EndDate")
    choices = end.find("{*}ValidValues/{*}DataSetReference")
    assert choices is not None
    assert (choices.findtext("{*}DataSetName"), choices.findtext("{*}ValueField")) == (
        "SnapshotDates",
        "Day",
    )
    dataset = next(d for d in report.findall(".//{*}DataSet") if d.get("Name") == "SnapshotDates")
    assert dataset.findtext("{*}Query/{*}CommandText") == "dbo.SnapshotDates"
    assert report.findtext(".//{*}Tablix/{*}NoRowsMessage")


def test_weighted_price_and_stock_on_closing_day(tmp_path):
    from ssrs_lab.data import generate
    from ssrs_lab.reference import summarize

    generate(tmp_path, seed=39)
    (tmp_path / "Sales.csv").write_text(
        "Day,StoreId,ProductId,Units,RevenueCents\n2026-01-01,1,1,1,100\n"
        "2026-01-02,1,1,3,600\n2026-01-02,2,1,5,900\n",
        encoding="utf8",
    )
    (tmp_path / "Stock.csv").write_text(
        "Day,StoreId,ProductId,OnHand\n2026-01-01,1,1,20\n2026-01-02,1,1,8\n2026-01-02,2,1,12\n",
        encoding="utf8",
    )
    result = summarize(tmp_path, "2026-01-01", "2026-01-02", stores=[1])
    assert result["total"] == {"Units": 4, "RevenueCents": 700, "PriceCents": 175, "OnHand": 8}
    assert result["groups"][0]["OnHand"] == 8


@pytest.mark.parametrize(
    "old,new",
    [
        ("Fields!RevenueCents.Value", "Fields!Typo.Value"),
        ("Parameters!EndDate.Value", "Parameters!Missing.Value"),
        ("dbo.ReportRows", "dbo.AbsentProc"),
        ("@CategoryIds", "@WrongArgument"),
        ("<DataField>OnHand</DataField>", "<DataField>Unknown</DataField>"),
    ],
)
def test_rdl_checker_rejects_drift(tmp_path, old, new):
    from ssrs_lab.contracts import check_rdl

    text = (ROOT / "rdl/SalesStock.rdl").read_text(encoding="utf8")
    assert old in text
    path = tmp_path / "changed.rdl"
    path.write_text(text.replace(old, new), encoding="utf8")
    with pytest.raises(ValueError):
        check_rdl(path, ROOT / "sql")


def test_pbip_is_reproducible_and_consistent(tmp_path):
    from ssrs_lab.pbip import write_project
    from ssrs_lab.contracts import check_pbip

    write_project(tmp_path)
    assert check_pbip(tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (ROOT / "powerbi" / path.relative_to(tmp_path)).read_bytes()
    visual = next(
        p for p in tmp_path.rglob("visual.json") if "Revenue" in p.read_text(encoding="utf8")
    )
    contents = json.loads(visual.read_text(encoding="utf8").replace("Revenue", "Missing"))
    visual.write_text(json.dumps(contents), encoding="utf8")
    with pytest.raises(ValueError):
        check_pbip(tmp_path)
