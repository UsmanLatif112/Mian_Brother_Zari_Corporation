"""View-only customer/vendor list + ledger helpers for the online portal.

Charts match the offline app: multi-series line on listing pages, doughnut
account summary on detail pages (built from synced ledger_entries).
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.date() if hasattr(value, "hour") else value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def list_balance_stats(rows) -> dict:
    """Totals for listing KPI cards (credit = owed to us, advance = we owe)."""
    credit = 0.0
    advance = 0.0
    for row in rows:
        bal = _f(row.get("balance") if hasattr(row, "get") else getattr(row, "balance", 0))
        if bal > 0:
            credit += bal
        elif bal < 0:
            advance += abs(bal)
    return {
        "count": len(rows),
        "total_credit": credit,
        "total_advance": advance,
    }


def row_get(row, name, default=None):
    if hasattr(row, "get"):
        return row.get(name, default)
    return getattr(row, name, default)


def attach_period_profit(rows, profit_map: dict) -> list[dict]:
    """Copy mapping rows into mutable dicts with period_profit."""
    out = []
    for row in rows:
        item = dict(row) if hasattr(row, "keys") else dict(row)
        pid = int(item.get("id") or 0)
        item["period_profit"] = _f(profit_map.get(pid, 0))
        out.append(item)
    return out


def filter_by_active(rows, active: str) -> list:
    active = (active or "all").lower()
    if active == "active":
        return [r for r in rows if row_get(r, "is_active") in (1, True, None)]
    if active == "inactive":
        return [r for r in rows if row_get(r, "is_active") in (0, False)]
    return list(rows)


def filter_by_customer_type(rows, ctype: str) -> list:
    ctype = (ctype or "all").strip().lower()
    if not ctype or ctype == "all":
        return list(rows)
    return [r for r in rows if str(row_get(r, "customer_type") or "good").lower() == ctype]


def filter_by_vendor_balance(rows, status: str) -> list:
    status = (status or "all").lower()
    if status == "payable":
        return [r for r in rows if _f(row_get(r, "balance")) > 0]
    if status == "prepaid":
        return [r for r in rows if _f(row_get(r, "balance")) < 0]
    if status == "settled":
        return [r for r in rows if abs(_f(row_get(r, "balance"))) < 0.0001]
    return list(rows)


def filter_by_profit(rows, profit: str) -> list:
    profit = (profit or "all").lower()
    if profit == "profit":
        return [r for r in rows if _f(row_get(r, "period_profit")) > 0]
    if profit == "loss":
        return [r for r in rows if _f(row_get(r, "period_profit")) < 0]
    if profit == "zero":
        return [r for r in rows if abs(_f(row_get(r, "period_profit"))) < 0.0001]
    return list(rows)


def profit_list_stats(rows) -> dict:
    total = sum(_f(row_get(r, "period_profit")) for r in rows)
    profit_n = sum(1 for r in rows if _f(row_get(r, "period_profit")) > 0)
    loss_n = sum(1 for r in rows if _f(row_get(r, "period_profit")) < 0)
    return {
        "total_period_profit": total,
        "profit_count": profit_n,
        "loss_count": loss_n,
        "list_profit": total if total > 0 else 0.0,
        "list_loss": (-total) if total < 0 else 0.0,
    }


def _iter_bucket_keys(start: date, end: date, *, daily: bool) -> list[str]:
    keys: list[str] = []
    if daily:
        d = start
        while d <= end:
            keys.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        return keys
    y, m = start.year, start.month
    end_y, end_m = end.year, end.month
    while (y, m) <= (end_y, end_m):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return keys


def _format_axis_label(bucket: str, *, daily: bool) -> str:
    if daily:
        try:
            return date.fromisoformat(bucket).strftime("%d %b")
        except ValueError:
            return bucket
    try:
        parts = bucket.split("-")
        if len(parts) >= 2:
            return date(int(parts[0]), int(parts[1]), 1).strftime("%b %Y")
    except (ValueError, TypeError):
        pass
    return bucket


def _bucket_end(bucket: str, *, daily: bool, chart_end: date) -> date:
    if daily:
        d = date.fromisoformat(bucket)
    else:
        y, m = map(int, bucket.split("-"))
        d = date(y, m, calendar.monthrange(y, m)[1])
    return min(d, chart_end)


def _bucket_key(d: date, *, daily: bool) -> str:
    return d.strftime("%Y-%m-%d") if daily else d.strftime("%Y-%m")


def _chart_range(period: str, start_s: str, end_s: str, earliest: date | None) -> tuple[date, date, bool]:
    today = date.today()
    end = _as_date(end_s) or today
    start = _as_date(start_s)
    if not start:
        if period == "all":
            start = earliest or (today - timedelta(days=29))
        else:
            start = end - timedelta(days=364)
    if start > end:
        start, end = end, start
    span = (end - start).days
    daily = span <= 31
    return start, end, daily


def _entry_field(e, name, default=0):
    if hasattr(e, "get"):
        return e.get(name, default)
    return getattr(e, name, default)


def _sum_party_balances_as_of(entries, as_of: date) -> tuple[float, float]:
    """Latest balance_after per party on/before as_of → (positive total, advance total)."""
    latest: dict[int, tuple[date, int, float]] = {}
    for e in entries:
        ed = _as_date(_entry_field(e, "entry_date"))
        if not ed or ed > as_of:
            continue
        pid = int(_entry_field(e, "party_id") or 0)
        if not pid:
            continue
        eid = int(_entry_field(e, "id") or 0)
        bal = _f(_entry_field(e, "balance_after"))
        prev = latest.get(pid)
        if prev is None or ed > prev[0] or (ed == prev[0] and eid >= prev[1]):
            latest[pid] = (ed, eid, bal)
    credit = 0.0
    advance = 0.0
    for _, _, bal in latest.values():
        if bal > 0:
            credit += bal
        elif bal < 0:
            advance += abs(bal)
    return credit, advance


def customer_list_chart(entries, *, period: str = "all", start_date: str = "", end_date: str = "") -> dict:
    """Line chart: Total Sale · Total Paid · Total Credit (same series as desktop)."""
    dates = [_as_date(_entry_field(e, "entry_date")) for e in entries]
    dates = [d for d in dates if d]
    earliest = min(dates) if dates else None
    chart_start, chart_end, daily = _chart_range(period, start_date, end_date, earliest)

    sale_map: dict[str, float] = defaultdict(float)
    paid_map: dict[str, float] = defaultdict(float)
    for e in entries:
        ed = _as_date(_entry_field(e, "entry_date"))
        if not ed or ed < chart_start or ed > chart_end:
            continue
        key = _bucket_key(ed, daily=daily)
        et = str(_entry_field(e, "entry_type") or "").lower()
        debit = _f(_entry_field(e, "debit"))
        credit = _f(_entry_field(e, "credit"))
        if et == "sale":
            sale_map[key] += debit
            paid_map[key] += credit
        elif et == "sale_return":
            sale_map[key] -= credit
            paid_map[key] -= debit
        elif et in ("account_settle", "advance", "sale_advance"):
            paid_map[key] += credit

    keys = _iter_bucket_keys(chart_start, chart_end, daily=daily)
    if daily and len(keys) > 62:
        keys = keys[-62:]
    elif period != "all" and not daily and len(keys) > 24:
        keys = keys[-24:]

    labels, sale, paid, credit_series = [], [], [], []
    for bucket in keys:
        as_of = _bucket_end(bucket, daily=daily, chart_end=chart_end)
        credit_f, _ = _sum_party_balances_as_of(entries, as_of)
        labels.append(_format_axis_label(bucket, daily=daily))
        sale.append(round(sale_map.get(bucket, 0.0), 2))
        paid.append(round(paid_map.get(bucket, 0.0), 2))
        credit_series.append(round(credit_f, 2))

    first = 0
    for i in range(len(keys)):
        if sale[i] or paid[i] or credit_series[i]:
            first = i
            break
    if first:
        labels, sale, paid, credit_series = labels[first:], sale[first:], paid[first:], credit_series[first:]

    return {
        "labels": labels,
        "sale": sale,
        "paid": paid,
        "credit": credit_series,
        "granularity": "day" if daily else "month",
    }


def vendor_list_chart(entries, *, period: str = "all", start_date: str = "", end_date: str = "") -> dict:
    """Line chart: Total Purchase · Paid · Payable (same series as desktop)."""
    dates = [_as_date(_entry_field(e, "entry_date")) for e in entries]
    dates = [d for d in dates if d]
    earliest = min(dates) if dates else None
    chart_start, chart_end, daily = _chart_range(period, start_date, end_date, earliest)

    total_map: dict[str, float] = defaultdict(float)
    paid_map: dict[str, float] = defaultdict(float)
    count_map: dict[str, float] = defaultdict(float)
    for e in entries:
        ed = _as_date(_entry_field(e, "entry_date"))
        if not ed or ed < chart_start or ed > chart_end:
            continue
        key = _bucket_key(ed, daily=daily)
        et = str(_entry_field(e, "entry_type") or "").lower()
        debit = _f(_entry_field(e, "debit"))
        credit = _f(_entry_field(e, "credit"))
        if et in ("purchase", "opening", "old_book", "vendor_opening"):
            total_map[key] += debit
            paid_map[key] += credit
            if debit or credit:
                count_map[key] += 1
        elif et in ("account_settle", "advance", "purchase_advance"):
            paid_map[key] += credit
        elif debit or credit:
            # Any other vendor movement still feeds the trend line
            total_map[key] += debit
            paid_map[key] += credit
            count_map[key] += 1

    keys = _iter_bucket_keys(chart_start, chart_end, daily=daily)
    if daily and len(keys) > 62:
        keys = keys[-62:]
    elif period != "all" and not daily and len(keys) > 24:
        keys = keys[-24:]

    labels, total, paid, payable, count = [], [], [], [], []
    for bucket in keys:
        as_of = _bucket_end(bucket, daily=daily, chart_end=chart_end)
        payable_f, _ = _sum_party_balances_as_of(entries, as_of)
        labels.append(_format_axis_label(bucket, daily=daily))
        total.append(round(total_map.get(bucket, 0.0), 2))
        paid.append(round(paid_map.get(bucket, 0.0), 2))
        payable.append(round(payable_f, 2))
        count.append(count_map.get(bucket, 0.0))

    first = 0
    for i in range(len(keys)):
        if total[i] or paid[i] or payable[i] or count[i]:
            first = i
            break
    if first:
        labels = labels[first:]
        total = total[first:]
        paid = paid[first:]
        payable = payable[first:]
        count = count[first:]

    return {
        "labels": labels,
        "total": total,
        "paid": paid,
        "payable": payable,
        "count": count,
        "granularity": "day" if daily else "month",
    }


def _pie_payload(items: list[dict]) -> dict:
    return {
        "labels": [i["name"] for i in items],
        "values": [float(i.get("value") or 0) for i in items],
        "rows": items,
    }


def _sum_debit(entries, *entry_types: str) -> Decimal:
    types = {t.lower() for t in entry_types}
    total = Decimal("0")
    for e in entries:
        et = str(_entry_field(e, "entry_type") or "").lower()
        if et in types:
            total += Decimal(str(_entry_field(e, "debit") or 0))
    return total


def _sum_credit(entries, *entry_types: str) -> Decimal:
    types = {t.lower() for t in entry_types}
    total = Decimal("0")
    for e in entries:
        et = str(_entry_field(e, "entry_type") or "").lower()
        if et in types:
            total += Decimal(str(_entry_field(e, "credit") or 0))
    return total


def customer_account_pie(entries, display_balance: float, *, ranged: bool = False) -> dict:
    """Pie slices match desktop: Total Sale, Total Paid, Total Credit, Advance."""
    period_hint = "In period" if ranged else "All time"
    total_sale = _sum_debit(entries, "sale") - _sum_credit(entries, "sale_return")
    if total_sale < 0:
        total_sale = Decimal("0")
    paid_at_sale = _sum_credit(entries, "sale")
    payments = _sum_credit(entries, "account_settle", "advance", "sale_advance")
    refunds = _sum_debit(entries, "sale_return")
    total_paid = paid_at_sale + payments - refunds
    if total_paid < 0:
        total_paid = Decimal("0")
    bal = Decimal(str(display_balance or 0))
    total_credit = max(bal, Decimal("0"))
    total_advance = max(-bal, Decimal("0"))

    rows = []
    if total_sale > 0:
        rows.append({"name": "Total Sale", "value": float(total_sale), "detail": period_hint})
    if total_paid > 0:
        rows.append({"name": "Total Paid", "value": float(total_paid), "detail": period_hint})
    if total_credit > 0:
        rows.append({"name": "Total Credit", "value": float(total_credit), "detail": "Outstanding"})
    if total_advance > 0:
        rows.append({"name": "Advance", "value": float(total_advance), "detail": "With us"})

    return {
        "pie": _pie_payload(rows),
        "total_sale": total_sale,
        "total_paid": total_paid,
        "total_credit": total_credit,
        "total_advance": total_advance,
    }


def vendor_account_pie(entries, display_balance: float, *, ranged: bool = False) -> dict:
    """Pie slices match desktop: Total Purchase, Total Paid, Prepaid Credit, Payable."""
    period_hint = "In period" if ranged else "All time"
    total_purchase = _sum_debit(entries, "purchase", "opening", "old_book", "vendor_opening")
    # Include any leftover vendor debits so the doughnut is never blank when data exists
    typed = {"purchase", "opening", "old_book", "vendor_opening", "account_settle", "advance", "purchase_advance"}
    for e in entries:
        et = str(_entry_field(e, "entry_type") or "").lower()
        if et not in typed:
            total_purchase += Decimal(str(_entry_field(e, "debit") or 0))
    total_paid = _sum_credit(entries, "account_settle", "advance", "purchase_advance")
    for e in entries:
        et = str(_entry_field(e, "entry_type") or "").lower()
        if et in ("purchase", "opening", "old_book", "vendor_opening"):
            total_paid += Decimal(str(_entry_field(e, "credit") or 0))
    bal = Decimal(str(display_balance or 0))
    total_payable = max(bal, Decimal("0"))
    total_credit = max(-bal, Decimal("0"))

    rows = []
    if total_purchase > 0:
        rows.append({"name": "Total Purchase", "value": float(total_purchase), "detail": period_hint})
    if total_paid > 0:
        rows.append({"name": "Total Paid", "value": float(total_paid), "detail": period_hint})
    if total_credit > 0:
        rows.append({"name": "Prepaid Credit", "value": float(total_credit), "detail": "Advance with vendor"})
    if total_payable > 0:
        rows.append({"name": "Total Payable", "value": float(total_payable), "detail": "Outstanding"})

    # Last resort: show raw debit/credit so detail graph still renders
    if not rows and entries:
        raw_debit = sum((_f(_entry_field(e, "debit")) for e in entries), 0.0)
        raw_credit = sum((_f(_entry_field(e, "credit")) for e in entries), 0.0)
        if raw_debit > 0:
            rows.append({"name": "Debit", "value": round(raw_debit, 2), "detail": period_hint})
        if raw_credit > 0:
            rows.append({"name": "Credit", "value": round(raw_credit, 2), "detail": period_hint})
        if abs(_f(display_balance)) > 0.0001:
            rows.append(
                {
                    "name": "Balance",
                    "value": round(abs(_f(display_balance)), 2),
                    "detail": "Payable" if _f(display_balance) > 0 else "Prepaid",
                }
            )

    return {
        "pie": _pie_payload(rows),
        "total_purchase": total_purchase,
        "total_paid": total_paid,
        "total_credit": total_credit,
        "total_payable": total_payable,
    }


# Back-compat alias used by older routes
def ledger_account_pie(entries, display_balance: float) -> dict:
    return customer_account_pie(entries, display_balance)


def entry_type_label(entry_type: str | None) -> str:
    return (entry_type or "—").replace("_", " ").title()


def money(value) -> str:
    return f"{_f(value):.2f}"
