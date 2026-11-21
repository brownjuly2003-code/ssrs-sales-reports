"""Run docs/check-measures.dax against an in-memory Excel Data Model (Windows, Excel, pywin32).

Power BI Desktop is not used. The Excel Data Model runs the same DAX language with the same
formulas, but it is a different product: the PBIP refresh, the report page and screenshots
are not checked here. Not part of the tests or CI: it needs Excel with the Data Model.

Steps:
1. checks that docs/check-measures.dax equals the query built by dax_check.build_query()
   (references: reference.json of docs/runs/offline-evidence.zip) and that the CSVs in data/
   (python -m ssrs_lab generate) have the SHA-256 recorded in the archive manifest;
2. starts its own Excel instance (DispatchEx, hidden, no alerts) and remembers its PID;
   saves an empty workbook into a new temporary folder outside the repository;
3. adds the DataFolder parameter and the Power Query source of every table from the TMDL
   partitions, loads each query into the Data Model; adds the relationships from
   relationships.tmdl;
4. defines all TMDL measures in a DEFINE block (formulas read from the TMDL files) and runs
   the control query through the Data Model connection; every row must be PASS;
5. negative control: two reference values are corrupted in a copy of the query; exactly
   these two rows must be FAIL;
6. writes <out>/dax-results.csv and <out>/dax-run.json; closes the workbook, quits its own
   Excel (taskkill /PID only for its own PID if it is still alive), deletes the temp folder.

Options: --formulas evaluates every measure alone (with the measures it references) before
the check, --formulas-only stops after that; --probe '<DAX query>' (repeatable) runs extra
queries, used on 21.11.2026 to find that this engine rejects COALESCE.

Usage (from the repository root, after python -m ssrs_lab generate):
python scripts/excel_dax_check.py [--out reports/dax]
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pythoncom
import win32com.client
import win32process

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "src"))
from ssrs_lab import dax_check  # noqa: E402

BROKEN = [("Revenue", "all"), ("Closing Stock", "partial / R1")]


def corrupt(query, values):
    """Copy of the query with two reference values changed."""
    for measure, context in BROKEN:
        value = dax_check._literal(values[measure, context])
        old = f'( "{measure}", "{context}", {value}, '
        new = f'( "{measure}", "{context}", {value} + 1, '
        assert query.count(old) == 1, old
        query = query.replace(old, new)
    return query


def needed(name, measures):
    """The measure and every measure it references, dependencies first."""
    by_name = {n: (t, n, f) for t, n, f in measures}
    order = []

    def visit(current):
        if current in (n for _, n, _ in order):
            return
        for ref in re.findall(r"(?<![\w\]'])\[([^\]]+)\]", by_name[current][2]):
            if ref in by_name:
                visit(ref)
        order.append(by_name[current])

    visit(name)
    return order


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(repo / "reports" / "dax"))
    parser.add_argument("--formulas", action="store_true")
    parser.add_argument("--formulas-only", action="store_true")
    parser.add_argument("--probe", action="append", default=[], help="extra DAX query")
    args = parser.parse_args()
    query = (repo / "docs" / "check-measures.dax").read_text(encoding="utf-8")
    assert query == dax_check.build_query(repo), "check-measures.dax is stale: write-dax-check"
    values = {(m, c): v for m, c, _, v in dax_check.control_rows()}
    data = repo / "data"
    with zipfile.ZipFile(repo / "docs/runs/offline-evidence.zip") as bundle:
        manifest = json.loads(bundle.read("manifest.json"))["sha256"]
    for name, digest in manifest.items():
        if name.endswith(".csv"):
            found = hashlib.sha256((data / name).read_bytes()).hexdigest()
            assert found == digest, f"data/{name} differs from the archive: ssrs_lab generate"
    model = repo / "powerbi" / "Retail.SemanticModel" / "definition"
    measures = dax_check.measure_definitions(model)
    define = dax_check.define_block(model)
    print(f"Query: {len(dax_check.parse_query(query))} rows, {len(measures)} measures", flush=True)

    pythoncom.CoInitialize()
    excel = win32com.client.DispatchEx("Excel.Application")
    _, pid = win32process.GetWindowThreadProcessId(excel.Hwnd)
    print("Own Excel PID", pid, "version", excel.Version, flush=True)
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.ScreenUpdating = False
    folder = Path(tempfile.mkdtemp(prefix="ssrs_dax_"))
    book = None
    try:
        book = excel.Workbooks.Add()
        book.SaveAs(str(folder / "dax_check.xlsx"), 51)
        book.Queries.Add("DataFolder", '"' + str(data) + '\\"')
        loaded = {}
        for file in sorted((model / "tables").glob("*.tmdl")):
            name = file.stem
            tmdl = file.read_text(encoding="utf-8")
            source = tmdl.split("\n\t\tsource =\n", 1)[1].split("\n\n", 1)[0]
            book.Queries.Add(name, "\n".join(line.strip() for line in source.splitlines()))
            connection = (
                "OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;"
                f'Location={name};Extended Properties=""'
            )
            book.Connections.Add2("Query - " + name, "", connection, name, 3, True, False)
            excel.CalculateUntilAsyncQueriesDone()
            loaded[name] = book.Model.ModelTables.Item(name).RecordCount
            print("Loaded", name, loaded[name], flush=True)
        relations = (model / "relationships.tmdl").read_text(encoding="utf-8")
        tables = book.Model.ModelTables
        pairs = re.findall(r"fromColumn: (\w+)\.(\w+)\s+toColumn: (\w+)\.(\w+)", relations)
        for child, foreign, parent, primary in pairs:
            book.Model.ModelRelationships.Add(
                tables.Item(child).ModelTableColumns.Item(foreign),
                tables.Item(parent).ModelTableColumns.Item(primary),
            )
        print("Relationships", book.Model.ModelRelationships.Count, flush=True)
        conn = book.Model.DataModelConnection.ModelConnection.ADOConnection

        def execute(dax, header=define):
            try:
                rs, _ = conn.Execute(header + dax)
            except Exception:
                print([(err.Number, err.Description) for err in conn.Errors], flush=True)
                raise
            try:
                return list(zip(*rs.GetRows(), strict=True))
            finally:
                rs.Close()

        if args.formulas or args.formulas_only:
            for table, name, formula in measures:
                lines = [f"MEASURE '{t}'[{n}] = {f}" for t, n, f in needed(name, measures)]
                header = "DEFINE\n" + "\n".join(lines) + "\n"
                try:
                    print(name, execute(f'EVALUATE ROW("Result", [{name}])', header), flush=True)
                except Exception:
                    print("Formula failed:", table, name, formula, flush=True)
            for probe in args.probe:
                try:
                    print("Probe", probe, execute(probe, ""), flush=True)
                except Exception:
                    print("Probe failed:", probe, flush=True)
            if args.formulas_only:
                return
        records = execute(query)
        expected = {(m, c): v for m, c, v, _ in dax_check.parse_query(query)}
        assert len(records) == len(expected), (len(records), len(expected))
        assert {(r[0], r[1]) for r in records} == set(expected)
        worst = 0.0
        for name, context, want, actual, result in records:
            reference = expected[name, context]
            assert (want is None) == (reference is None), (name, context)
            if reference is not None:
                assert abs(float(want) - reference) <= 1e-9 * max(1, abs(reference))
                if actual is not None:
                    worst = max(worst, abs(float(actual) - reference))
            print(name, "|", context, "|", want, "|", actual, "|", result, flush=True)
        failed = [(r[0], r[1]) for r in records if r[-1] != "PASS"]
        print("FAIL rows:", failed, flush=True)
        negative = sorted((r[0], r[1]) for r in execute(corrupt(query, values)) if r[-1] == "FAIL")
        print("Negative control FAIL rows:", negative, flush=True)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        with (out / "dax-results.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["measure", "context", "expected", "actual", "result"])
            writer.writerows(records)
        run = {
            "excel_version": excel.Version,
            "rows_loaded": loaded,
            "relationships": book.Model.ModelRelationships.Count,
            "measures": len(measures),
            "control_rows": len(records),
            "passed": len(records) - len(failed),
            "failed": [list(item) for item in failed],
            "max_abs_difference": worst,
            "negative_control_corrupted": [list(item) for item in BROKEN],
            "negative_control_failed": [list(item) for item in negative],
            "define_sha256": dax_check.text_sha256(define),
            "query_sha256": dax_check.text_sha256(query),
        }
        (out / "dax-run.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(json.dumps(run, ensure_ascii=False, indent=2), flush=True)
        assert not failed and negative == sorted(BROKEN)
    finally:
        if book is not None:
            book.Close(False)
        excel.Quit()
        del excel
        pythoncom.CoUninitialize()
        time.sleep(3)
        alive = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        if "EXCEL.EXE" in alive.upper():
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False)
            print("Own Excel killed by PID", pid, flush=True)
        shutil.rmtree(folder, ignore_errors=True)
        print("Temp folder removed:", not folder.exists(), flush=True)


if __name__ == "__main__":
    main()
