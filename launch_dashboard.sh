#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "Generating Cash Flow Marco artifacts..."
python3 cash_flow_marco.py \
  --balances-csv balances.csv \
  --transactions-csv transactions.csv \
  --sheet-csv sheet_expenses.csv \
  --output-dir artifacts

echo "Launching dashboard server..."
echo "URL: http://127.0.0.1:8765/cash_flow_marco.html"
python3 dashboard_server.py --host 127.0.0.1 --port 8765 --open-browser
