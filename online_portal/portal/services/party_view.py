"""View-only customer/vendor list + ledger helpers for the online portal."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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


def list_chart_payload(rows, *, party_label: str = "Customers") -> dict:
    """Bar chart: credit vs advance for the listed parties."""
    stats = list_balance_stats(rows)
    return {
        "labels": ["Total Credit", "Advance Balance"],
        "values": [round(stats["total_credit"], 2), round(stats["total_advance"], 2)],
        "party_label": party_label,
    }


def ledger_account_pie(entries, display_balance: float) -> dict:
    """Simple debit/credit pie for ledger detail (view-only)."""
    total_debit = sum(_f(e.get("debit") if hasattr(e, "get") else getattr(e, "debit", 0)) for e in entries)
    total_credit = sum(_f(e.get("credit") if hasattr(e, "get") else getattr(e, "credit", 0)) for e in entries)
    rows = []
    if total_debit:
        rows.append({"name": "Debit (out / sales)", "value": round(total_debit, 2), "detail": "Period total"})
    if total_credit:
        rows.append({"name": "Credit (in / paid)", "value": round(total_credit, 2), "detail": "Period total"})
    bal = _f(display_balance)
    if abs(bal) > 0.0001:
        rows.append(
            {
                "name": "Balance",
                "value": round(abs(bal), 2),
                "detail": "Receivable" if bal > 0 else "Advance",
            }
        )
    by_type: dict[str, float] = defaultdict(float)
    for e in entries:
        et = (e.get("entry_type") if hasattr(e, "get") else getattr(e, "entry_type", None)) or "other"
        amt = _f(e.get("debit") if hasattr(e, "get") else getattr(e, "debit", 0)) + _f(
            e.get("credit") if hasattr(e, "get") else getattr(e, "credit", 0)
        )
        by_type[str(et)] += amt
    return {
        "pie": {"rows": rows},
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "by_type": {k: round(v, 2) for k, v in by_type.items()},
    }


def entry_type_label(entry_type: str | None) -> str:
    return (entry_type or "—").replace("_", " ").title()


def money(value) -> str:
    return f"{_f(value):.2f}"
