import csv
import hashlib
from pathlib import Path

from sqlglot import exp, parse_one

from ssrs_lab.data import HEADERS, generate


def test_seeded_csv_matches_ddl_and_is_repeatable(tmp_path):
    import re

    generate(tmp_path)
    hashes = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in tmp_path.glob("*.csv")}
    generate(tmp_path)
    assert hashes == {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in tmp_path.glob("*.csv")
    }
    generate(tmp_path, seed=41)
    assert hashlib.sha256((tmp_path / "Sales.csv").read_bytes()).hexdigest() != hashes["Sales.csv"]
    ddl = Path("sql/01-schema.sql").read_text(encoding="utf-8")
    load = Path("sql/02-load.sql").read_text(encoding="utf-8")
    for name, fields in HEADERS.items():
        create = re.search(rf"CREATE TABLE dbo\.{name} \(.*?\n    \);", ddl, re.S)[0]
        schema = parse_one(create, read="tsql").this
        declared = [c.name for c in schema.expressions if isinstance(c, exp.ColumnDef)]
        with (tmp_path / f"{name}.csv").open(encoding="utf-8") as stream:
            assert next(csv.reader(stream)) == declared == fields
        target = f"dbo.{name}"
        if fields[-1].endswith("Name"):
            target = f"#{name}Csv"
            columns = ", ".join(fields)
            assert f"INSERT INTO dbo.{name} ({columns}) SELECT {columns} FROM {target};" in load
        assert f"BULK INSERT {target} FROM '/var/opt/mssql/import/{name}.csv'" in load


def test_subscription_request_and_fault_handling():
    from ssrs_lab.subscription import create_subscription
    from xml.etree import ElementTree as ET
    import json
    import pytest

    config = json.loads(Path("subscription/example.json").read_bytes())
    seen = []

    def send(action, envelope):
        seen.append((action, ET.fromstring(envelope)))
        return b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><CreateSubscriptionResponse xmlns="http://schemas.microsoft.com/sqlserver/reporting/2010/03/01/ReportServer"><SubscriptionID>example-id</SubscriptionID></CreateSubscriptionResponse></soap:Body></soap:Envelope>'

    assert create_subscription(config, send) == "example-id"
    action, request = seen[0]
    assert action.endswith("/CreateSubscription")
    assert request.findtext(".//{*}ItemPath") == config["report"]
    assert request.findtext(".//{*}EventType") == "TimedSubscription"
    assert (
        ET.fromstring(request.findtext(".//{*}MatchData")).findtext("DailyRecurrence/DaysInterval")
        == "1"
    )
    pairs = [
        (p.findtext("{*}Name"), p.findtext("{*}Value"))
        for p in request.findall(".//{*}Parameters/{*}ParameterValue")
    ]
    assert ("EndDate", "2026-01-31") in pairs
    assert [v for k, v in pairs if k == "StoreIds"] == ["1", "2"]
    settings = request.find(".//{*}ParameterValues")
    assert {p.findtext("{*}Name"): p.findtext("{*}Value") for p in settings}[
        "RenderFormat"
    ] == "PDF"
    with pytest.raises(ValueError, match="denied"):
        create_subscription(
            config,
            lambda *_: (
                b"<Envelope><Body><Fault><faultstring>denied</faultstring></Fault></Body></Envelope>"
            ),
        )
    with pytest.raises(ValueError):
        create_subscription(config, lambda *_: b"<Envelope/>")


def test_subscription_endpoint_needs_https_except_on_localhost():
    import pytest

    from ssrs_lab.subscription import check_endpoint

    for url in (
        "https://server.example.invalid/ReportServer/ReportService2010.asmx",
        "http://localhost/ReportServer/ReportService2010.asmx",
        "http://127.0.0.1:8080/ReportServer/ReportService2010.asmx",
    ):
        assert check_endpoint(url) == url
    for url in (
        "http://server.example.invalid/ReportServer/ReportService2010.asmx",
        "http://localhost.example.invalid/ReportServer/ReportService2010.asmx",
        "ftp://localhost/ReportServer",
    ):
        with pytest.raises(ValueError, match="HTTPS"):
            check_endpoint(url)


def test_runner_subscription_matches_the_north_case():
    import json
    import zipfile

    from ssrs_lab.subscription import create_subscription

    config = json.loads(Path("subscription/windows-runner.json").read_bytes())
    replay = Path("docs/runs/2026-11-19-render/replay.zip")
    with zipfile.ZipFile(replay) as archive:
        north = json.loads(archive.read("north.json"))["Parameters"]
    assert config["parameters"] == north
    assert config["format"] == "EXCELOPENXML"
    assert config["to"].endswith(".invalid")
    reply = b"<Envelope><Body><CreateSubscriptionResponse><SubscriptionID>id</SubscriptionID></CreateSubscriptionResponse></Body></Envelope>"
    assert create_subscription(config, lambda *_: reply) == "id"
