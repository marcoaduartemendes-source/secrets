#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import urllib.parse
import urllib.request
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


def parse_float(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def read_balances(path: Path | None) -> dict[str, float]:
    if not path or not path.exists():
        return {}
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        for r in rows:
            acct = (r.get("account_last4") or r.get("last4") or "").strip()
            if acct in TARGET_ACCOUNTS:
                bal = parse_float(r.get("balance") or r.get("current_balance") or "0")
                if bal is not None:
                    out[acct] = bal
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
            amt = parse_float(r.get("amount") or r.get("signed_amount") or "0")
            if amt is None:
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
            amt = parse_float(r.get("amount") or r.get("expense") or "0")
            if amt is None:
                continue
            records.append({"date": d, "description": desc, "amount": -abs(amt), "source": "sheet"})
    return records


def export_google_sheet_csv(sheet_url: str, dest: Path) -> None:
    parsed = urllib.parse.urlparse(sheet_url)
    if "docs.google.com" not in parsed.netloc:
        raise ValueError("Google Sheet URL must be from docs.google.com")

    gid = urllib.parse.parse_qs(parsed.query).get("gid", ["0"])[0]
    base = sheet_url.split("/edit")[0]
    export_url = f"{base}/export?format=csv&gid={gid}"
    req = urllib.request.Request(export_url, headers={"User-Agent": "CashFlowMarco/1.0"})

    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8", errors="replace")
    dest.write_text(content, encoding="utf-8")


def export_monarch_csvs(email: str, password: str, balances_dest: Path, tx_dest: Path) -> None:
    try:
        from monarchmoney import MonarchMoney  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "monarchmoney package not installed. Run: pip install monarchmoney"
        ) from exc

    mm = MonarchMoney()
    mm.login(email, password)

    accounts = mm.get_accounts()
    transactions = mm.get_transactions(limit=5000)

    with balances_dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["account_last4", "balance"])
        w.writeheader()
        for a in accounts:
            name = str(a.get("displayName") or a.get("name") or "")
            last4 = re.findall(r"(\d{4})", name)
            acct_last4 = (a.get("last4") or (last4[-1] if last4 else "") or "").strip()
            balance = a.get("currentBalance") or a.get("displayBalance") or a.get("balance") or 0
            if acct_last4 in TARGET_ACCOUNTS:
                w.writerow({"account_last4": acct_last4, "balance": balance})

    with tx_dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "description", "amount", "account_last4"])
        w.writeheader()
        for t in transactions:
            date = str(t.get("date") or t.get("postedDate") or "")
            desc = str(t.get("merchant") or t.get("originalName") or t.get("name") or "Unknown")
            amount = t.get("amount") or t.get("signedAmount") or 0
            account = t.get("account") or {}
            name = str(account.get("displayName") or account.get("name") or "")
            last4 = re.findall(r"(\d{4})", name)
            acct_last4 = (account.get("last4") or (last4[-1] if last4 else "") or "").strip()
            if acct_last4 in TARGET_ACCOUNTS:
                w.writerow({"date": date, "description": desc, "amount": amount, "account_last4": acct_last4})


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
    month = dt.date(start.year, start.month, 1)
    for _ in range(12):
        pay_date = dt.date(month.year, month.month, 25)
        if start <= pay_date <= end:
            events.append({"date": pay_date, "description": "Massachusetts Tax Bill", "amount": -40000.0, "source": "manual"})
        month = dt.date(month.year + (month.month // 12), (month.month % 12) + 1, 1)

    month = dt.date(2026, 5, 1)
    while month <= end:
        if month >= start:
            events.append({"date": month, "description": "Mortgage", "amount": -19561.60, "source": "manual"})
        month = dt.date(month.year + (month.month // 12), (month.month % 12) + 1, 1)


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


def write_html(path: Path, title: str, start_cash: float, weeks: list[dict], balances: dict[str, float], warnings: list[str]) -> None:
    data = {
        "title": title,
        "startCash": round(start_cash, 2),
        "weeks": weeks,
        "balances": balances,
        "warnings": warnings,
        "generatedAt": dt.datetime.utcnow().isoformat() + "Z",
    }
    html = f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>{title}</title>
  <style>
    body {{ font-family: Inter, Arial, sans-serif; margin: 20px; background:#f7f9fc; color:#10243e; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }}
    button {{ background:#0b5fff; color:#fff; border:none; border-radius:8px; padding:10px 14px; cursor:pointer; }}
    button:disabled {{ background:#9db7ff; cursor:not-allowed; }}
    .cards {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:12px; margin-top:14px; }}
    .card {{ background:#fff; border:1px solid #e6ecf5; border-radius:10px; padding:12px; }}
    .muted {{ color:#5b6b82; font-size:12px; }}
    .warn {{ background:#fff7e8; border:1px solid #f8d188; border-radius:8px; padding:10px; margin-top:10px; }}
    svg {{ width:100%; height:300px; border:1px solid #e6ecf5; background:#fff; border-radius:10px; margin-top:12px; }}
    .tabs {{ margin:14px 0; display:flex; gap:8px; }}
    .tabs button {{ background:#eef3ff; color:#0b5fff; }}
    table {{ border-collapse: collapse; width:100%; background:#fff; font-size:12px; }}
    th, td {{ border:1px solid #e6ecf5; padding:7px; text-align:right; vertical-align:top; }}
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3), th:last-child, td:last-child {{ text-align:left; }}
    .neg {{ color:#b00020; font-weight:600; }}
    .pos {{ color:#0a7f30; font-weight:600; }}
  </style>
</head>
<body>
  <div class='top'>
    <div>
      <h1 style='margin:0'>{title}</h1>
      <div class='muted'>Generated at <span id='generated'></span></div>
    </div>
    <div>
      <button id='refreshBtn' onclick='refreshData()'>🔄 Refresh from Monarch + Google Sheet</button>
      <div class='muted' id='refreshState'></div>
    </div>
  </div>

  <div id='warnings'></div>
  <div class='cards' id='summary'></div>
  <svg id='chart' viewBox='0 0 1000 300'></svg>

  <div class='tabs'>
    <button onclick='renderTable(13)'>13 weeks</button>
    <button onclick='renderTable(26)'>26 weeks</button>
    <button onclick='renderTable(52)'>52 weeks</button>
  </div>
  <table id='tbl'></table>
<script>
let data = {json.dumps(data)};

function fmt(n) {{
  const s = new Intl.NumberFormat('en-US', {{style:'currency', currency:'USD'}}).format(n);
  const klass = n < 0 ? 'neg' : 'pos';
  return `<span class='${{klass}}'>${{s}}</span>`;
}}

function renderWarnings() {{
  const el = document.getElementById('warnings');
  if (!data.warnings || !data.warnings.length) {{ el.innerHTML = ''; return; }}
  el.innerHTML = data.warnings.map(w => `<div class='warn'>⚠️ ${{w}}</div>`).join('');
}}

function renderSummary() {{
  document.getElementById('generated').textContent = data.generatedAt;
  const wk13 = data.weeks[Math.min(12, data.weeks.length-1)]?.ending_cash ?? data.startCash;
  const wk26 = data.weeks[Math.min(25, data.weeks.length-1)]?.ending_cash ?? data.startCash;
  const wk52 = data.weeks[data.weeks.length-1]?.ending_cash ?? data.startCash;
  const cards = [
    ['Starting cash', fmt(data.startCash)],
    ['Projected cash (13w)', fmt(wk13)],
    ['Projected cash (26w)', fmt(wk26)],
    ['Projected cash (52w)', fmt(wk52)],
    ['Tracked accounts', Object.keys(data.balances).map(k=>`${{k}}: $${{data.balances[k].toFixed(2)}}`).join('<br>') || 'No balances loaded'],
  ];
  document.getElementById('summary').innerHTML = cards.map(([k,v]) => `<div class='card'><div class='muted'>${{k}}</div><div style='font-size:20px;margin-top:6px'>${{v}}</div></div>`).join('');
}}

function renderChart() {{
  const svg = document.getElementById('chart');
  const w = 1000, h = 300, pad = 40;
  const vals = data.weeks.map(x=>x.ending_cash);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = Math.max(1, max-min);

  const points = vals.map((v,i)=> {{
    const x = pad + i*((w-2*pad)/(vals.length-1));
    const y = h-pad - ((v-min)/span)*(h-2*pad);
    return `${{x.toFixed(1)}},${{y.toFixed(1)}}`;
  }}).join(' ');

  const zeroY = h-pad - ((0-min)/span)*(h-2*pad);
  svg.innerHTML = `
    <line x1='${{pad}}' y1='${{zeroY}}' x2='${{w-pad}}' y2='${{zeroY}}' stroke='#d8e2f2' stroke-dasharray='4 4'/>
    <polyline points='${{points}}' fill='none' stroke='#0b5fff' stroke-width='3'/>
  `;
}}

function renderTable(n) {{
  const rows = data.weeks.slice(0,n);
  let html = `<tr><th>Week</th><th>Start</th><th>End</th><th>Inflows</th><th>Outflows</th><th>Net</th><th>Ending Cash</th><th>Details</th></tr>`;
  for (const r of rows) {{
    const det = (r.events || []).map(e => `${{e.date}} · ${{e.description}} (${{Number(e.amount).toFixed(2)}})`).join('<br>');
    html += `<tr><td>${{r.week}}</td><td>${{r.start}}</td><td>${{r.end}}</td><td>${{fmt(r.inflow)}}</td><td>${{fmt(r.outflow)}}</td><td>${{fmt(r.net)}}</td><td>${{fmt(r.ending_cash)}}</td><td>${{det || '-'}}</td></tr>`;
  }}
  document.getElementById('tbl').innerHTML = html;
}}

async function reloadFromJson() {{
  const r = await fetch('cash_flow_marco.json?ts='+Date.now());
  if (!r.ok) throw new Error('Could not load updated JSON artifact.');
  data = await r.json();
  renderWarnings(); renderSummary(); renderChart(); renderTable(13);
}}

async function refreshData() {{
  const btn = document.getElementById('refreshBtn');
  const state = document.getElementById('refreshState');
  btn.disabled = true;
  state.textContent = 'Refreshing data...';
  try {{
    const r = await fetch('/api/refresh', {{method: 'POST'}});
    const payload = await r.json();
    if (!r.ok || payload.ok === false) throw new Error(payload.message || 'Refresh failed');
    await reloadFromJson();
    state.textContent = 'Refresh completed.';
  }} catch (e) {{
    state.textContent = 'Refresh unavailable from file:// mode. Start dashboard_server.py to enable.';
  }} finally {{
    btn.disabled = false;
  }}
}}

renderWarnings(); renderSummary(); renderChart(); renderTable(13);
</script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def build_projection(
    balances_csv: Path | None,
    transactions_csv: Path | None,
    sheet_csv: Path | None,
    output_dir: Path,
    refresh: bool = False,
    google_sheet_url: str | None = None,
    monarch_email: str | None = None,
    monarch_password: str | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if refresh and google_sheet_url:
        try:
            export_google_sheet_csv(google_sheet_url, sheet_csv or Path("sheet_expenses.csv"))
        except Exception as exc:
            warnings.append(f"Google Sheet refresh failed: {exc}")

    if refresh and monarch_email and monarch_password:
        try:
            export_monarch_csvs(monarch_email, monarch_password, balances_csv or Path("balances.csv"), transactions_csv or Path("transactions.csv"))
        except Exception as exc:
            warnings.append(f"Monarch refresh failed: {exc}")

    balances = read_balances(balances_csv)
    start_cash = sum(balances.get(a, 0.0) for a in TARGET_ACCOUNTS)

    tx = read_transactions(transactions_csv)
    recurring = detect_recurring_expenses(tx)
    sheet_expenses = read_sheet_expenses(sheet_csv)

    start = TODAY
    end = start + dt.timedelta(weeks=52, days=-1)

    events = gen_schedule(recurring, start, end)
    events.extend(sheet_expenses)
    add_fixed_events(start, end, events)
    events.sort(key=lambda x: x["date"])

    weeks = bucket_weekly(start_cash, events, start, weeks=52)

    payload = {
        "title": "Cash Flow Marco",
        "generatedAt": dt.datetime.utcnow().isoformat() + "Z",
        "startCash": round(start_cash, 2),
        "balances": balances,
        "weeks": weeks,
        "events": events,
        "warnings": warnings,
    }

    json_path = output_dir / "cash_flow_marco.json"
    html_path = output_dir / "cash_flow_marco.html"
    json_path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    write_html(html_path, payload["title"], start_cash, weeks, balances, warnings)

    return {
        "json_path": str(json_path),
        "html_path": str(html_path),
        "recurring_count": len(recurring),
        "warnings": warnings,
    }


def print_dashboard_summary(json_path: Path, weeks_to_show: int = 13) -> None:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    title = payload.get("title", "Cash Flow Marco")
    start_cash = float(payload.get("startCash", 0))
    weeks = payload.get("weeks", [])

    w13 = weeks[12]["ending_cash"] if len(weeks) >= 13 else start_cash
    w26 = weeks[25]["ending_cash"] if len(weeks) >= 26 else start_cash
    w52 = weeks[51]["ending_cash"] if len(weeks) >= 52 else (weeks[-1]["ending_cash"] if weeks else start_cash)

    print()
    print(f"{title} — Dashboard Snapshot")
    print("=" * 56)
    print(f"Starting cash: ${start_cash:,.2f}")
    print(f"13-week ending cash: ${w13:,.2f}")
    print(f"26-week ending cash: ${w26:,.2f}")
    print(f"52-week ending cash: ${w52:,.2f}")
    print("-" * 56)
    print("Week | Start       | End         | Inflows     | Outflows    | Ending Cash")
    print("-" * 56)

    for row in weeks[:weeks_to_show]:
        print(
            f"{row['week']:>4} | {row['start']} | {row['end']} | "
            f"${row['inflow']:>10,.2f} | ${row['outflow']:>10,.2f} | ${row['ending_cash']:>11,.2f}"
        )
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Build weekly cash-flow projections (13/26/52 weeks).")
    p.add_argument("--balances-csv", type=Path, default=Path("balances.csv"), help="CSV with account_last4,balance")
    p.add_argument("--transactions-csv", type=Path, default=Path("transactions.csv"), help="CSV export from Monarch transactions")
    p.add_argument("--sheet-csv", type=Path, default=Path("sheet_expenses.csv"), help="CSV export from Google Sheet expenses")
    p.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    p.add_argument("--refresh", action="store_true", help="Refresh source data from Monarch + Google Sheet before recalculating")
    p.add_argument("--show-dashboard", action="store_true", help="Print a terminal dashboard snapshot after generating artifacts")
    p.add_argument("--google-sheet-url", default=os.getenv("GOOGLE_SHEET_URL"), help="Google Sheet URL used for CSV refresh")
    p.add_argument("--monarch-email", default=os.getenv("MONARCH_EMAIL"), help="Monarch login email")
    p.add_argument("--monarch-password", default=os.getenv("MONARCH_PASSWORD"), help="Monarch login password")
    args = p.parse_args()

    result = build_projection(
        balances_csv=args.balances_csv,
        transactions_csv=args.transactions_csv,
        sheet_csv=args.sheet_csv,
        output_dir=args.output_dir,
        refresh=args.refresh,
        google_sheet_url=args.google_sheet_url,
        monarch_email=args.monarch_email,
        monarch_password=args.monarch_password,
    )

    print(f"Created: {result['json_path']}")
    print(f"Created: {result['html_path']}")
    print(f"Detected recurring expense patterns: {result['recurring_count']}")
    for w in result["warnings"]:
        print(f"Warning: {w}")

    if args.show_dashboard:
        print_dashboard_summary(Path(result["json_path"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
