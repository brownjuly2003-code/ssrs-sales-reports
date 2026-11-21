"""Offline checks of scripts/check_server.py and scripts/smtp_sink.py.

The server answers are fabricated from the seeded CSV or taken from the committed
ReportViewer exports. On the Windows runner the same tests read the live answers:
SSRS_CASCADE_DIR (cascade-north.json, cascade-both.json) and SSRS_MAIL_XLSX (the
attachment of the subscription e-mail) replace the fabricated inputs, so a changed
answer must fail there too.
"""

import importlib.util
import json
import os
import smtplib
import socket
import time
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import openpyxl
import pytest

from ssrs_lab.data import generate

ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "docs" / "runs" / "2026-11-19-render"
CASCADE_DIR = os.environ.get("SSRS_CASCADE_DIR")
MAIL_XLSX = Path(os.environ.get("SSRS_MAIL_XLSX", RENDER / "north.xlsx"))
TO = "reports@example.invalid"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_server = load("check_server")
smtp_sink = load("smtp_sink")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    folder = tmp_path_factory.mktemp("data")
    generate(folder)
    return folder


def fabricated(data, regions):
    stores = check_server.expected_stores(data, regions)
    days = [date.fromisoformat(d) for d in check_server.snapshot_days(data)]
    return [
        {"Name": "StartDate", "ValidValues": [], "ErrorMessage": None},
        {
            "Name": "EndDate",
            "ValidValues": [
                {"Label": f"{d:%d.%m.%Y}", "Value": f"{d.month}/{d.day}/{d.year} 12:00:00 AM"}
                for d in days
            ],
        },
        {"Name": "RegionIds", "ValidValues": [{"Label": "Север", "Value": "1"}]},
        {"Name": "StoreIds", "ValidValues": [{"Label": n, "Value": i} for i, n in stores]},
        {"Name": "CategoryIds", "ValidValues": [{"Label": "Бакалея", "Value": "2"}]},
    ]


def cascade(data, regions, name):
    if CASCADE_DIR:
        return check_server.load_dump(Path(CASCADE_DIR) / name)
    return {p["Name"]: p for p in fabricated(data, regions)}


@pytest.mark.parametrize(
    ("regions", "name"), [([1], "cascade-north.json"), ([1, 2], "cascade-both.json")]
)
def test_cascade_answer_passes_and_changes_fail(data, regions, name):
    answer = cascade(data, regions, name)
    assert check_server.cascade_failures(answer, data, regions) == []
    stores = answer["StoreIds"]["ValidValues"]

    swapped = json.loads(json.dumps(answer))
    swapped["StoreIds"]["ValidValues"] = [stores[1], stores[0], *stores[2:]]
    assert check_server.cascade_failures(swapped, data, regions)

    extra = json.loads(json.dumps(answer))
    extra["StoreIds"]["ValidValues"].append({"Label": "Магазин 09", "Value": "9"})
    assert check_server.cascade_failures(extra, data, regions)

    late = json.loads(json.dumps(answer))
    late["EndDate"]["ValidValues"].append({"Label": "x", "Value": "2026-02-01T00:00:00"})
    assert check_server.cascade_failures(late, data, regions)

    gap = json.loads(json.dumps(answer))
    del gap["EndDate"]["ValidValues"][5]
    assert check_server.cascade_failures(gap, data, regions)

    other = [3 - r for r in regions] if len(regions) == 1 else [1]
    assert check_server.cascade_failures(answer, data, other)


def test_cascade_dump_file_and_errors(data, tmp_path):
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(fabricated(data, [1]), ensure_ascii=False), encoding="utf-8-sig")
    assert (
        check_server.main(["cascade", "--dump", str(dump), "--regions", "1", "--data", str(data)])
        == 0
    )
    assert (
        check_server.main(["cascade", "--dump", str(dump), "--regions", "2", "--data", str(data)])
        == 1
    )
    broken = fabricated(data, [1])
    broken[3]["ErrorMessage"] = "rsErrorOpeningConnection"
    dump.write_text(json.dumps(broken), encoding="utf-8")
    assert (
        check_server.main(["cascade", "--dump", str(dump), "--regions", "1", "--data", str(data)])
        == 1
    )


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("2026-01-31T00:00:00", "2026-01-31"),
        ("1/31/2026 12:00:00 AM", "2026-01-31"),
        ("31.01.2026 0:00:00", "2026-01-31"),
        ("January", None),
    ],
)
def test_server_date_formats(text, day):
    assert check_server.parse_day(text) == day


def test_header_needs_names_not_ids(data):
    line = check_server.selection_line(data, [1, 2], [2, 6], [2, 1, 4, 3])
    assert line == (
        "Регионы: Север, Юг; магазины: Магазин 02, Магазин 06; "
        "категории: Бакалея, Напитки, Овощи, Фрукты"
    )
    wrapped = "Страница 1 из 1\nРегионы: Север, Юг; магазины: Магазин\n02, Магазин 06; категории: Бакалея, Напитки, Овощи, Фрукты"
    assert check_server.header_failures(wrapped, data, [1, 2], [2, 6], [2, 1, 4, 3]) == []
    assert check_server.header_failures(wrapped, data, [1, 2], [2, 5], [2, 1, 4, 3])
    ids = "Регионы: 1, 2; магазины: 2, 6; категории: 2, 1, 4, 3"
    assert check_server.header_failures(ids, data, [1, 2], [2, 6], [2, 1, 4, 3])


def test_committed_local_pdf_prints_ids_and_fails(data):
    # ReportViewer in local mode printed parameter values instead of labels.
    assert not check_server.check_header(
        RENDER / "all.pdf", data, [1, 2], list(range(1, 9)), [2, 1, 4, 3]
    )


def write_mail(folder, workbook, to=TO, subject=check_server.SUBJECT, name="SalesStock.xlsx"):
    folder.mkdir(parents=True, exist_ok=True)
    message = EmailMessage()
    message["From"] = "ssrs@example.invalid"
    message["To"] = to
    message["Subject"] = subject
    message.set_content("The report is attached.")
    if workbook is not None:
        message.add_attachment(
            Path(workbook).read_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=name,
        )
    (folder / f"{len(list(folder.glob('*.eml'))) + 1:03}.eml").write_bytes(bytes(message))


def test_mail_attachment_is_recounted(tmp_path):
    write_mail(tmp_path / "mail", MAIL_XLSX)
    assert check_server.check_mail(tmp_path / "mail", TO, "north", tmp_path / "out")
    assert (tmp_path / "out" / "north.xlsx").read_bytes() == MAIL_XLSX.read_bytes()


def test_changed_attachment_fails(tmp_path):
    book = openpyxl.load_workbook(MAIL_XLSX)
    sheet = book.active
    total = next(i for i in range(1, sheet.max_row + 1) if sheet.cell(i, 1).value == "Всего")
    sheet.cell(total, 3).value += 1
    changed = tmp_path / "changed.xlsx"
    book.save(changed)
    write_mail(tmp_path / "mail", changed)
    assert not check_server.check_mail(tmp_path / "mail", TO, "north", tmp_path / "out")


def test_attachment_of_another_case_fails(tmp_path):
    write_mail(tmp_path / "mail", RENDER / "all.xlsx")
    assert not check_server.check_mail(tmp_path / "mail", TO, "north", tmp_path / "out")


@pytest.mark.parametrize(
    "change",
    [
        {"to": "someone@example.invalid"},
        {"subject": "Other"},
        {"workbook": None},
        {"name": "SalesStock.pdf"},
    ],
)
def test_wrong_message_fails(tmp_path, change):
    write_mail(tmp_path / "mail", **{"workbook": MAIL_XLSX, **change})
    assert not check_server.check_mail(tmp_path / "mail", TO, "north", tmp_path / "out")


def test_two_messages_to_the_recipient_fail(tmp_path):
    write_mail(tmp_path / "mail", MAIL_XLSX)
    write_mail(tmp_path / "mail", MAIL_XLSX)
    assert not check_server.check_mail(tmp_path / "mail", TO, "north", tmp_path / "out")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_smtp_sink_stores_the_message(tmp_path):
    port = free_port()
    controller = smtp_sink.start(tmp_path / "mail", "127.0.0.1", port)
    try:
        assert (tmp_path / "mail" / "ready").read_text(
            encoding="utf-8"
        ).strip() == f"127.0.0.1:{port}"
        message = EmailMessage()
        message["From"] = "ssrs@example.invalid"
        message["To"] = TO
        message["Subject"] = check_server.SUBJECT
        message.set_content("body")
        message.add_attachment(
            MAIL_XLSX.read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename="SalesStock.xlsx",
        )
        with smtplib.SMTP("127.0.0.1", port, timeout=10) as client:
            client.send_message(message)
        for _ in range(50):
            if list((tmp_path / "mail").glob("*.eml")):
                break
            time.sleep(0.1)
    finally:
        controller.stop()
    meta = json.loads((tmp_path / "mail" / "001.json").read_text(encoding="utf-8"))
    assert meta["rcpt_tos"] == [TO]
    assert check_server.check_mail(tmp_path / "mail", TO, "north", tmp_path / "out")
