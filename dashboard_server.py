#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ARTIFACTS), **kwargs)

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

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

        env = os.environ.copy()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        ok = proc.returncode == 0
        payload = {
            "ok": ok,
            "message": "Refresh completed" if ok else "Refresh failed",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        self._json(200 if ok else 500, payload)


if __name__ == "__main__":
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Cash Flow dashboard server running on http://127.0.0.1:8765/cash_flow_marco.html")
    server.serve_forever()
