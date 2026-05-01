# Cash Flow Marco

Local cash-flow project to model weekly cash position across **13, 26, and 52 weeks**, with a user-friendly dashboard and one-click refresh.

## What this does
- Uses balances from selected cash accounts (`0974`, `7029`, `6278`, `6512`) as starting cash.
- Detects recurring expenses from Monarch transactions when there is a pattern spanning **>3 months** and at least 3 occurrences.
- Adds planned sheet expenses from a Google Sheet CSV export.
- Applies manual constraints requested:
  - Massachusetts Tax Bill: **$40,000 monthly**, paid on the 25th (next 12 months).
  - Mortgage: **$19,561.60 monthly** starting **2026-05-01**.
- Produces:
  - `artifacts/cash_flow_marco.json`
  - `artifacts/cash_flow_marco.html` (interactive visual artifact)

## Input files
Use CSV exports (local files):

1. `balances.csv`
   - Columns: `account_last4,balance`
2. `transactions.csv`
   - Columns: `date,description,amount,account_last4`
3. `sheet_expenses.csv`
   - Columns: `date,description,amount`

Only account last4 values `0974`, `7029`, `6278`, `6512` are used for starting cash and transaction filtering.

## Build artifacts
```bash
python3 cash_flow_marco.py \
  --balances-csv balances.csv \
  --transactions-csv transactions.csv \
  --sheet-csv sheet_expenses.csv \
  --output-dir artifacts
```

Then open:
- `artifacts/cash_flow_marco.html`

If you want the dashboard shown directly in terminal output as well:
```bash
python3 cash_flow_marco.py \
  --balances-csv balances.csv \
  --transactions-csv transactions.csv \
  --sheet-csv sheet_expenses.csv \
  --output-dir artifacts \
  --show-dashboard
```

## Enable one-click refresh button (Monarch + Google Sheet)
The HTML includes a **Refresh from Monarch + Google Sheet** button. For the button to work, run the local dashboard server:

```bash
export MONARCH_EMAIL='your_email@example.com'
export MONARCH_PASSWORD='your_password'
export GOOGLE_SHEET_URL='https://docs.google.com/spreadsheets/d/.../edit?gid=...'

python3 dashboard_server.py --open-browser
```

Open:
- `http://127.0.0.1:8765/cash_flow_marco.html`
- `http://127.0.0.1:8765/` (auto-redirects to the dashboard)

When you click refresh, it runs:
- `python3 cash_flow_marco.py --refresh ...`

This will attempt to:
1. Pull latest expenses CSV from Google Sheets export URL.
2. Pull latest balances/transactions from Monarch (via `monarchmoney` Python package).
3. Recompute and rewrite the JSON + HTML artifacts.

## Dependency for Monarch refresh
Install once (if not already installed):
```bash
pip install monarchmoney
```

## Important note
If direct login to private apps fails (2FA, MFA, package limitations, network policy), the dashboard still works with local CSV exports and will show warnings in the artifact.


### Quick launch (opens browser automatically)
```bash
python3 dashboard_server.py --open-browser
```

If artifacts are missing, `dashboard_server.py` now auto-generates
`artifacts/cash_flow_marco.html` and `artifacts/cash_flow_marco.json` at startup.

Or run everything (rebuild + launch) with one command:
```bash
bash launch_dashboard.sh
```
