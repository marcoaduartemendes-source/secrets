#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.path = "/cash_flow_marco.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/refresh":
            self._json(404, {"ok": False, "message": "Not found"})
            return

        cmd = [
            "python3",
            str(ROOT / "cash_flow_marco.py"),
            "--refresh",
            "--balances-csv",
            str(ROOT / "balances.csv"),
            "--transactions-csv",
            str(ROOT / "transactions.csv"),
            "--sheet-csv",
            str(ROOT / "sheet_expenses.csv"),
            "--output-dir",
            str(ARTIFACTS),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
        ok = proc.returncode == 0
        payload = {
            "ok": ok,
            "message": "Refresh completed" if ok else "Refresh failed",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        self._json(200 if ok else 500, payload)


def ensure_artifacts_exist() -> None:
    html_path = ARTIFACTS / "cash_flow_marco.html"
    json_path = ARTIFACTS / "cash_flow_marco.json"
    if html_path.exists() and json_path.exists():
        return

    cmd = [
        "python3",
        str(ROOT / "cash_flow_marco.py"),
        "--balances-csv",
        str(ROOT / "balances.csv"),
        "--transactions-csv",
        str(ROOT / "transactions.csv"),
        "--sheet-csv",
        str(ROOT / "sheet_expenses.csv"),
        "--output-dir",
        str(ARTIFACTS),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(
            "Could not generate dashboard artifacts automatically.\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Cash Flow Marco artifacts and refresh endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="Open the dashboard URL in the default browser")
    args = parser.parse_args()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    ensure_artifacts_exist()
    handler = lambda *a, **kw: Handler(*a, directory=str(ARTIFACTS), **kw)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/cash_flow_marco.html"
    print(f"Cash Flow dashboard server running on {url}")

    if args.open_browser:
        webbrowser.open(url, new=2)
        print("Attempted to open dashboard in your default browser.")

    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
