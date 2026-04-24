#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import median

TARGET_ACCOUNTS = {"0974", "7029", "6278", "6512"}
TODAY = dt.date.today()


def parse_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def normalize_desc(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\d+", "", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def read_balances(path: Path | None) -> dict[str, float]:
    if not path or not path.exists():
        return {}
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        for r in rows:
            acct = (r.get("account_last4") or r.get("last4") or "").strip()
            if acct in TARGET_ACCOUNTS:
                bal = r.get("balance") or r.get("current_balance") or "0"
                try:
                    out[acct] = float(str(bal).replace(",", ""))
                except ValueError:
                    pass
    return out


def read_transactions(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    tx = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        for r in rows:
            d = parse_date(r.get("date") or r.get("posted_date") or "")
            if not d:
                continue
            acct = (r.get("account_last4") or r.get("last4") or "").strip()
            if acct and acct not in TARGET_ACCOUNTS:
                continue
            desc = r.get("description") or r.get("merchant") or "Unknown"
            amt_txt = r.get("amount") or r.get("signed_amount") or "0"
            try:
                amt = float(str(amt_txt).replace(",", ""))
            except ValueError:
                continue
            tx.append({"date": d, "description": desc, "amount": amt, "account_last4": acct})
    return tx


def read_sheet_expenses(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    records = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        for r in rows:
            d = parse_date(r.get("date") or r.get("due_date") or "")
            if not d:
                continue
            desc = r.get("description") or r.get("name") or "Sheet Expense"
            amt_txt = r.get("amount") or r.get("expense") or "0"
            try:
                amt = float(str(amt_txt).replace(",", ""))
            except ValueError:
                continue
            records.append({"date": d, "description": desc, "amount": -abs(amt)})
    return records


def detect_recurring_expenses(transactions: list[dict]) -> list[dict]:
    by_desc: dict[str, list[dict]] = defaultdict(list)
    for t in transactions:
        if t["amount"] >= 0:
            continue
        key = normalize_desc(t["description"])
        if key:
            by_desc[key].append(t)

    rec = []
    for key, items in by_desc.items():
        if len(items) < 3:
            continue
        items.sort(key=lambda x: x["date"])
        span_days = (items[-1]["date"] - items[0]["date"]).days
        if span_days < 90:
            continue
        deltas = [(items[i]["date"] - items[i - 1]["date"]).days for i in range(1, len(items))]
        if not deltas:
            continue
        med = median(deltas)
        if med <= 10:
            freq = "weekly"
            step_days = 7
        elif med <= 20:
            freq = "biweekly"
            step_days = 14
        else:
            freq = "monthly"
            step_days = 30
        if max(abs(d - med) for d in deltas) > 12 and freq != "monthly":
            continue
        amount = -abs(median([x["amount"] for x in items]))
        rec.append(
            {
                "description": key.title(),
                "amount": amount,
                "frequency": freq,
                "step_days": step_days,
                "last_seen": items[-1]["date"],
                "start_date": items[-1]["date"] + dt.timedelta(days=step_days),
            }
        )
    return rec


def gen_schedule(recurring: list[dict], start: dt.date, end: dt.date) -> list[dict]:
    events = []
    for r in recurring:
        d = max(start, r["start_date"])
        while d <= end:
            events.append({"date": d, "description": r["description"], "amount": r["amount"], "source": "recurring"})
            d += dt.timedelta(days=r["step_days"])
    return events


def add_fixed_events(start: dt.date, end: dt.date, events: list[dict]) -> None:
    # Massachusetts Tax Bill: 40k monthly, paid on 25th, for next 12 months
    month = dt.date(start.year, start.month, 1)
    for _ in range(12):
        pay_date = dt.date(month.year, month.month, 25)
        if start <= pay_date <= end:
            events.append({"date": pay_date, "description": "Massachusetts Tax Bill", "amount": -40000.0, "source": "manual"})
        if month.month == 12:
            month = dt.date(month.year + 1, 1, 1)
        else:
            month = dt.date(month.year, month.month + 1, 1)

    # Mortgage increase starting May 1, 2026
    month = dt.date(2026, 5, 1)
    while month <= end:
        if month >= start:
            events.append({"date": month, "description": "Mortgage", "amount": -19561.60, "source": "manual"})
        if month.month == 12:
            month = dt.date(month.year + 1, 1, 1)
        else:
            month = dt.date(month.year, month.month + 1, 1)


def bucket_weekly(start_cash: float, events: list[dict], start: dt.date, weeks: int = 52) -> list[dict]:
    by_day: dict[dt.date, list[dict]] = defaultdict(list)
    for e in events:
        by_day[e["date"]].append(e)

    rows = []
    bal = start_cash
    for w in range(weeks):
        ws = start + dt.timedelta(days=w * 7)
        we = ws + dt.timedelta(days=6)
        inflow = 0.0
        outflow = 0.0
        detail = []
        d = ws
        while d <= we:
            for ev in by_day.get(d, []):
                amt = ev["amount"]
                bal += amt
                if amt >= 0:
                    inflow += amt
                else:
                    outflow += amt
                detail.append(
                    {
                        "date": d.isoformat(),
                        "description": ev["description"],
                        "amount": ev["amount"],
                        "source": ev.get("source", "unknown"),
                    }
                )
            d += dt.timedelta(days=1)

        rows.append(
            {
                "week": w + 1,
                "start": ws.isoformat(),
                "end": we.isoformat(),
                "inflow": round(inflow, 2),
                "outflow": round(outflow, 2),
                "net": round(inflow + outflow, 2),
                "ending_cash": round(bal, 2),
                "events": detail,
            }
        )
    return rows


def write_html(path: Path, title: str, start_cash: float, weeks: list[dict], balances: dict[str, float]) -> None:
    data = {
        "title": title,
        "startCash": round(start_cash, 2),
        "weeks": weeks,
        "balances": balances,
    }
    html = f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    .cards {{ display:flex; gap:12px; flex-wrap:wrap; }}
    .card {{ border:1px solid #ddd; border-radius:10px; padding:12px; min-width:200px; }}
    table {{ border-collapse: collapse; width:100%; margin-top:12px; font-size:12px; }}
    th, td {{ border:1px solid #ddd; padding:6px; text-align:right; }}
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) {{ text-align:left; }}
    .neg {{ color:#b00020; font-weight:600; }}
    .tabs button {{ margin-right:8px; }}
    svg {{ width:100%; height:280px; border:1px solid #ddd; border-radius:8px; margin-top:12px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class='cards' id='summary'></div>
  <svg id='chart' viewBox='0 0 800 280'></svg>
  <div class='tabs'>
    <button onclick='renderTable(13)'>13 weeks</button>
    <button onclick='renderTable(26)'>26 weeks</button>
    <button onclick='renderTable(52)'>52 weeks</button>
  </div>
  <table id='tbl'></table>
<script>
const data = {json.dumps(data)};

function fmt(n) {{
  const s = new Intl.NumberFormat('en-US', {{style:'currency', currency:'USD'}}).format(n);
  return n < 0 ? `<span class='neg'>${{s}}</span>` : s;
}}

function renderSummary() {{
  const latest = data.weeks[data.weeks.length-1].ending_cash;
  const cards = [
    ['Starting cash', fmt(data.startCash)],
    ['Projected cash (52w)', fmt(latest)],
    ['Tracked accounts', Object.keys(data.balances).map(k=>`${{k}}: $${{data.balances[k].toFixed(2)}}`).join('<br>') || 'No balance file provided'],
  ];
  document.getElementById('summary').innerHTML = cards.map(([k,v]) => `<div class='card'><div><b>${{k}}</b></div><div>${{v}}</div></div>`).join('');
}}

function renderChart() {{
  const svg = document.getElementById('chart');
  const w = 800, h = 280, pad = 30;
  const vals = data.weeks.map(x=>x.ending_cash);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = Math.max(1, max-min);
  const points = vals.map((v,i)=> {{
    const x = pad + i*((w-2*pad)/(vals.length-1));
    const y = h-pad - ((v-min)/span)*(h-2*pad);
    return `${{x.toFixed(1)}},${{y.toFixed(1)}}`;
  }}).join(' ');
  svg.innerHTML = `<polyline points='${{points}}' fill='none' stroke='#0b5fff' stroke-width='2'/>`;
}}

function renderTable(n) {{
  const rows = data.weeks.slice(0,n);
  let html = `<tr><th>Week</th><th>Start</th><th>End</th><th>Inflows</th><th>Outflows</th><th>Net</th><th>Ending Cash</th><th>Details</th></tr>`;
  for (const r of rows) {{
    const det = r.events.map(e => `${{e.date}} - ${{e.description}} (${{e.amount.toFixed(2)}})`).join('<br>');
    html += `<tr><td>${{r.week}}</td><td>${{r.start}}</td><td>${{r.end}}</td><td>${{fmt(r.inflow)}}</td><td>${{fmt(r.outflow)}}</td><td>${{fmt(r.net)}}</td><td>${{fmt(r.ending_cash)}}</td><td style='text-align:left'>${{det}}</td></tr>`;
  }}
  document.getElementById('tbl').innerHTML = html;
}}
renderSummary(); renderChart(); renderTable(13);
</script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Build weekly cash-flow projections (13/26/52 weeks).")
    p.add_argument("--balances-csv", type=Path, help="CSV with account_last4,balance")
    p.add_argument("--transactions-csv", type=Path, help="CSV export from Monarch transactions")
    p.add_argument("--sheet-csv", type=Path, help="CSV export from Google Sheet expenses")
    p.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = p.parse_args()

    balances = read_balances(args.balances_csv)
    start_cash = sum(balances.get(a, 0.0) for a in TARGET_ACCOUNTS)

    tx = read_transactions(args.transactions_csv)
    recurring = detect_recurring_expenses(tx)
    sheet_expenses = read_sheet_expenses(args.sheet_csv)

    start = TODAY
    end = start + dt.timedelta(weeks=52, days=-1)

    events = gen_schedule(recurring, start, end)
    events.extend(sheet_expenses)
    add_fixed_events(start, end, events)
    events.sort(key=lambda x: x["date"])

    weeks = bucket_weekly(start_cash, events, start, weeks=52)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "cash_flow_marco.json"
    html_path = args.output_dir / "cash_flow_marco.html"
    json_path.write_text(json.dumps({"start_cash": start_cash, "weeks": weeks, "events": events}, default=str, indent=2), encoding="utf-8")
    write_html(html_path, "Cash Flow Marco", start_cash, weeks, balances)

    print(f"Created: {json_path}")
    print(f"Created: {html_path}")
    print(f"Detected recurring expense patterns: {len(recurring)}")
    if not args.transactions_csv or not args.sheet_csv or not args.balances_csv:
        print("Note: one or more input files were missing; projection includes only provided data plus manual tax/mortgage assumptions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
