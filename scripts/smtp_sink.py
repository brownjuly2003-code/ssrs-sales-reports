"""Local SMTP receiver for the subscription check on the Windows runner.

Accepts every message on HOST:PORT and stores it unchanged as NNN.eml in the output folder,
with NNN.json beside it (envelope sender, recipients, size). Nothing is relayed. The folder
gets a file "ready" once the server listens; the process runs until it is stopped.

    python scripts/smtp_sink.py --host 127.0.0.1 --port 25 --out reports/ssrs/mail
"""

import argparse
import json
import threading
from pathlib import Path

from aiosmtpd.controller import Controller


class Sink:
    def __init__(self, folder: Path):
        self.folder = folder
        self.count = 0
        self.lock = threading.Lock()

    async def handle_DATA(self, server, session, envelope):  # noqa: N802 (aiosmtpd hook name)
        with self.lock:
            self.count += 1
            stem = self.folder / f"{self.count:03}"
        content = envelope.original_content or b""
        stem.with_suffix(".eml").write_bytes(content)
        meta = {
            "mail_from": envelope.mail_from,
            "rcpt_tos": envelope.rcpt_tos,
            "bytes": len(content),
        }
        stem.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(
            f"received {stem.name}.eml from {envelope.mail_from} to {envelope.rcpt_tos}", flush=True
        )
        return "250 Message accepted for delivery"


def start(folder: Path, host: str, port: int) -> Controller:
    folder.mkdir(parents=True, exist_ok=True)
    controller = Controller(Sink(folder), hostname=host, port=port)
    controller.start()
    (folder / "ready").write_text(f"{host}:{port}\n", encoding="utf-8")
    return controller


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=25)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    controller = start(args.out, args.host, args.port)
    print(f"SMTP sink listening on {args.host}:{args.port}, messages go to {args.out}", flush=True)
    try:
        threading.Event().wait()
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
