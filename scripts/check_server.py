"""Check the answers of a live SSRS server against the seeded CSV and the offline reference.

Run on the Windows runner (.github/workflows/ssrs-windows.yml); tested offline on fabricated
and committed inputs (tests/test_check_server.py).

cascade  GetItemParameters dump (scripts/Get-ReportParameters.ps1): with the given regions
         the valid stores are exactly the stores of these regions, ordered by name as the
         procedure dbo.Stores returns them; the valid end dates are exactly the stock
         snapshot dates (dbo.SnapshotDates).
header   a rendered PDF prints the selection line of the page header with names, not ids:
         "Регионы: ...; магазины: ...; категории: ..." in the order the ids were passed.
mail     the subscription e-mail: one message to the recipient with the report subject and
         one XLSX attachment; the attachment is saved as <case>.xlsx and recounted by
         scripts/check_render.py (totals, group rows, product rows, prices, fills).

Exit code 0 means every check passed.
"""

import argparse
import csv
import email
import email.policy
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

import pypdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_render  # noqa: E402

SUBJECT = "Retail sales and closing inventory"
DAY_PATTERNS = (
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), (1, 2, 3)),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})"), (3, 1, 2)),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})"), (3, 2, 1)),
)


def read_csv(data, table):
    with (Path(data) / f"{table}.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def expected_stores(data, regions):
    rows = [r for r in read_csv(data, "Store") if int(r["RegionId"]) in set(regions)]
    return [(r["StoreId"], r["StoreName"]) for r in sorted(rows, key=lambda r: r["StoreName"])]


def snapshot_days(data):
    return sorted({r["Day"] for r in read_csv(data, "Stock")})


def parse_day(text):
    """ISO date of a DateTime value as SSRS prints it (ISO, en-US or ru-RU), else None."""
    for pattern, (y, m, d) in DAY_PATTERNS:
        found = pattern.match(str(text).strip())
        if found:
            parts = found.groups()
            return date(int(parts[y - 1]), int(parts[m - 1]), int(parts[d - 1])).isoformat()
    return None


def load_dump(path):
    parameters = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(parameters, dict):
        parameters = [parameters]
    return {p["Name"]: p for p in parameters}


def cascade_failures(parameters, data, regions):
    failures = []
    for name in ("RegionIds", "StoreIds", "CategoryIds", "EndDate"):
        if name not in parameters:
            failures.append(f"parameter {name} is missing")
        elif parameters[name].get("ErrorMessage"):
            failures.append(f"{name}: {parameters[name]['ErrorMessage']}")
    if failures:
        return failures
    stores = [(str(v["Value"]), v["Label"]) for v in parameters["StoreIds"]["ValidValues"] or []]
    want = expected_stores(data, regions)
    if stores != want:
        failures.append(f"valid stores for regions {list(regions)}: {stores} != {want}")
    ends = [parse_day(v["Value"]) for v in parameters["EndDate"]["ValidValues"] or []]
    if ends != snapshot_days(data):
        failures.append(f"valid end dates {ends[:3]}... ({len(ends)}) != snapshot dates")
    if any(day is None or not day.startswith("2026-01-") for day in ends):
        failures.append("an end date outside January 2026 or unreadable")
    return failures


def check_cascade(dump, data, regions):
    parameters = load_dump(dump)
    failures = cascade_failures(parameters, data, regions)
    stores = [v["Label"] for v in parameters.get("StoreIds", {}).get("ValidValues") or []]
    ends = parameters.get("EndDate", {}).get("ValidValues") or []
    print(
        f"cascade regions={list(regions)}: stores {stores}; end dates {len(ends)} "
        f"({ends[0]['Value'] if ends else '-'} .. {ends[-1]['Value'] if ends else '-'}) "
        f"-> {'FAIL' if failures else 'ok'}"
    )
    for failure in failures:
        print(f"  {failure}")
    return not failures


def selection_line(data, regions, stores, categories):
    names = {
        t: {r[f"{t}Id"]: r[f"{t}Name"] for r in read_csv(data, t)}
        for t in ("Region", "Store", "Category")
    }

    def join(table, ids):
        return ", ".join(names[table][str(i)] for i in ids)

    return (
        f"Регионы: {join('Region', regions)}; магазины: {join('Store', stores)}; "
        f"категории: {join('Category', categories)}"
    )


def header_failures(text, data, regions, stores, categories):
    flat = " ".join(text.replace("\xa0", " ").split())
    want = selection_line(data, regions, stores, categories)
    if want not in flat:
        found = re.search(r"Регионы:.*?категории:[^\n]{0,80}", flat)
        return [f"header {found.group(0) if found else 'not found'!r} != {want!r}"]
    return []


def check_header(pdf, data, regions, stores, categories):
    text = "\n".join(page.extract_text() for page in pypdf.PdfReader(pdf).pages)
    failures = header_failures(text, data, regions, stores, categories)
    print(f"header of {Path(pdf).name}: {'FAIL' if failures else 'ok'}")
    for failure in failures:
        print(f"  {failure}")
    return not failures


def read_mail(maildir, to):
    """The single message to the recipient and its single XLSX attachment."""
    failures, matching = [], []
    for path in sorted(Path(maildir).glob("*.eml")):
        message = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        if to.lower() in str(message.get("To", "")).lower():
            matching.append((path, message))
    if len(matching) != 1:
        return None, None, [f"{len(matching)} messages to {to} in {maildir}, expected 1"]
    path, message = matching[0]
    if str(message.get("Subject", "")).strip() != SUBJECT:
        failures.append(f"subject {message.get('Subject')!r} != {SUBJECT!r}")
    books = [
        part
        for part in message.iter_attachments()
        if (part.get_filename() or "").lower().endswith(".xlsx")
    ]
    if len(books) != 1:
        failures.append(f"{len(books)} XLSX attachments, expected 1")
        return path, None, failures
    return path, books[0], failures


def check_mail(maildir, to, case, out):
    path, book, failures = read_mail(maildir, to)
    passed = False
    if book is not None:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{case}.xlsx").write_bytes(book.get_payload(decode=True))
        with zipfile.ZipFile(check_render.EVIDENCE) as archive:
            reference = json.loads(archive.read("reference.json"))
        with zipfile.ZipFile(check_render.RENDER / "replay.zip") as replay:
            passed = check_render.check_case(
                case, reference[case], replay, out, check_render.captions()
            )
    name = path.name if path else "-"
    attachment = book.get_filename() if book is not None else "-"
    ok = passed and not failures
    print(f"mail {name}: attachment {attachment} recounted as '{case}' -> {'ok' if ok else 'FAIL'}")
    for failure in failures:
        print(f"  {failure}")
    return ok


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cascade = commands.add_parser("cascade")
    cascade.add_argument("--dump", type=Path, required=True)
    cascade.add_argument("--regions", type=int, nargs="+", required=True)
    header = commands.add_parser("header")
    header.add_argument("--pdf", type=Path, required=True)
    for name in ("--regions", "--stores", "--categories"):
        header.add_argument(name, type=int, nargs="+", required=True)
    mail = commands.add_parser("mail")
    mail.add_argument("--maildir", type=Path, required=True)
    mail.add_argument("--to", required=True)
    mail.add_argument("--case", choices=sorted(check_render.CASES), required=True)
    mail.add_argument("--out", type=Path, required=True)
    for command in (cascade, header):
        command.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    if args.command == "cascade":
        passed = check_cascade(args.dump, args.data, args.regions)
    elif args.command == "header":
        passed = check_header(args.pdf, args.data, args.regions, args.stores, args.categories)
    else:
        passed = check_mail(args.maildir, args.to, args.case, args.out)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
