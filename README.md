# Cash Flow Marco

Local cash-flow project to model weekly cash position across **13, 26, and 52 weeks**.

## What this does
- Uses balances from selected cash accounts (`0974`, `7029`, `6278`, `6512`) as starting cash.
- Detects recurring expenses from Monarch transactions when there is a pattern spanning **>3 months** and at least 3 occurrences.
- Adds planned sheet expenses from a Google Sheet CSV export.
- Applies manual constraints requested:
  - Massachusetts Tax Bill: **$40,000 monthly**, paid on the 25th (next 12 months).
  - Mortgage: **$19,561.60 monthly** starting **2026-05-01**.
- Produces:
  - `artifacts/cash_flow_marco.json`
  - `artifacts/cash_flow_marco.html` (visual artifact)

## Input files
Use CSV exports (local files):

1. `balances.csv`
   - Columns: `account_last4,balance`
2. `transactions.csv`
   - Columns: `date,description,amount,account_last4`
3. `sheet_expenses.csv`
   - Columns: `date,description,amount`

Only account last4 values `0974`, `7029`, `6278`, `6512` are used for starting cash and transaction filtering.

## Run
```bash
python3 cash_flow_marco.py \
  --balances-csv balances.csv \
  --transactions-csv transactions.csv \
  --sheet-csv sheet_expenses.csv
```

Then open:
- `artifacts/cash_flow_marco.html`

## Important note
This environment cannot directly log into private web apps (Monarch) or private Google Sheets on your behalf. Export those files and place them locally, then run the script.
