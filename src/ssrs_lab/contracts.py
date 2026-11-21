"""Static checks for the stored procedure, RDL and PBIP contracts."""

import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlglot import exp, parse_one

RDL = "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sql_contracts(sql_dir):
    procedures = {}
    source = (sql_dir / "03-datasets.sql").read_text(encoding="utf-8")
    for batch in re.split(r"(?im)^GO\s*$", source):
        if not batch.strip():
            continue
        match = re.fullmatch(
            r"\s*CREATE OR ALTER PROCEDURE\s+(dbo\.\w+)(.*?)\s+AS\s+(.*)", batch, re.I | re.S
        )
        require(match, "Invalid procedure declaration")
        name, args, body = match.groups()
        select = parse_one(body, read="tsql")
        require(isinstance(select, exp.Select), f"Expected SELECT in {name}")
        procedures[name] = set(re.findall(r"@\w+", args)), set(select.named_selects)
    return procedures


def check_rdl(path, sql_dir):
    root = ET.parse(path).getroot()
    require(root.tag == f"{{{RDL}}}Report", "Expected RDL 2016")
    for node in root.iter():
        node.tag = node.tag.removeprefix(f"{{{RDL}}}")
    parents = {child: parent for parent in root.iter() for child in parent}
    params = {p.get("Name") for p in root.findall("ReportParameters/ReportParameter")}
    procedures = sql_contracts(sql_dir)
    sources = {s.get("Name") for s in root.findall("DataSources/DataSource")}
    datasets = {}
    for dataset in root.findall("DataSets/DataSet"):
        name = dataset.get("Name")
        query = dataset.find("Query")
        proc = query.findtext("CommandText")
        require(
            query.findtext("DataSourceName") in sources
            and query.findtext("CommandType") == "StoredProcedure",
            f"Invalid query in {name}",
        )
        require(proc in procedures, f"Unknown procedure {proc}")
        arguments, columns = procedures[proc]
        bindings = {
            p.get("Name"): p.findtext("Value")
            for p in query.findall("QueryParameters/QueryParameter")
        }
        require(bindings.keys() == arguments, f"Procedure arguments differ for {proc}")
        for argument, binding in bindings.items():
            key = argument[1:]
            expected = (
                f'=Join(Parameters!{key}.Value, ",")'
                if key.endswith("Ids")
                else f"=Parameters!{key}.Value"
            )
            require(binding == expected, f"Wrong parameter expression: {argument}")
        fields = {f.get("Name"): f.findtext("DataField") for f in dataset.findall("Fields/Field")}
        require(set(fields.values()) <= columns, f"Unknown column in {name}")
        datasets[name] = fields
    for ref in root.iter("DataSetReference"):
        name = ref.findtext("DataSetName")
        fields = [ref.findtext("ValueField")]
        if ref.find("LabelField") is not None:
            fields.append(ref.findtext("LabelField"))
        require(
            name in datasets and all(f in datasets[name] for f in fields),
            f"Invalid parameter lookup {name}",
        )
    boxes = {t.get("Name") for t in root.iter("Textbox")}
    for node in root.iter():
        text = node.text or ""
        if node.tag == "ToggleItem":
            require(text in boxes, f"Unknown toggle {text}")
        if node.tag == "Tablix":
            require(node.findtext("DataSetName") in datasets, "Unknown tablix dataset")
        if not text.startswith("="):
            continue
        require(set(re.findall(r"Parameters!(\w+)", text)) <= params, f"Unknown parameter: {text}")
        fields = set(re.findall(r"Fields!(\w+)", text))
        if fields:
            container = node
            while container in parents and container.tag != "Tablix":
                container = parents[container]
            ds = container.findtext("DataSetName")
            require(
                ds in datasets and fields <= datasets[ds].keys(), f"Unknown field in {ds}: {text}"
            )
    return len(datasets)


def check_pbip(root: Path, data=None):
    project = json.loads((root / "Retail.pbip").read_bytes())
    report = root / project["artifacts"][0]["report"]["path"]
    reference = json.loads((report / "definition.pbir").read_bytes())
    model = report / reference["datasetReference"]["byPath"]["path"] / "definition"
    columns, measures = {}, {}
    for table in (model / "tables").glob("*.tmdl"):
        text = table.read_text(encoding="utf-8")
        name = table.stem
        require(f"table {name}\n" in text, f"Missing table {name}")
        fields = re.findall(r"(?m)^\tcolumn (.+)$", text)
        columns[name] = set(fields)
        measures[name] = {m.strip("'") for m in re.findall(r"(?m)^\tmeasure (.+?) = ", text)}
        source = re.search(r'File\.Contents\(DataFolder & "([^"]+\.csv)"\)', text)
        require(source, f"Missing CSV source for {name}")
        if data:
            with (data / source[1]).open(encoding="utf-8", newline="") as stream:
                require(next(csv.reader(stream)) == fields, f"Column mismatch for {name}")
    rel = (model / "relationships.tmdl").read_text(encoding="utf-8")
    links = re.findall(r"fromColumn: (\w+)\.(\w+)\s+toColumn: (\w+)\.(\w+)", rel)
    expected = {
        (fact, key, dim, key)
        for fact in ("Sales", "Stock")
        for dim, key in (("Store", "StoreId"), ("Product", "ProductId"), ("Calendar", "Day"))
    }
    require(set(links) == expected, "Invalid star relationships")
    for a, b, c, d in links:
        require(b in columns[a] and d in columns[c], "Unknown relationship endpoint")
    visuals = list(report.rglob("visual.json"))
    require(bool(visuals), "Report has no visuals")
    for file in visuals:
        state = json.loads(file.read_bytes())["visual"]["query"]["queryState"]
        for role in state.values():
            for projection in role["projections"]:
                kind, field = next(iter(projection["field"].items()))
                table = field["Expression"]["SourceRef"]["Entity"]
                items = {"Column": columns, "Measure": measures}[kind]
                require(
                    field["Property"] in items.get(table, set()),
                    f"Unknown {table}.{field['Property']}",
                )
    return len(visuals)
