"""Customer open dues for Record Payment unpaid picker."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.models import Customer, CustomerReceiving, Sale, SaleItem
from app.models.sales import PaymentStatus
from app.services.journal_service import items_particulars
from app.services.sale_service import _sale_particulars_notes


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _sale_due(sale: Sale) -> Decimal:
    grand = _money(sale.grand_total)
    paid = _money(sale.amount_paid)
    applied = min(paid, grand) if paid > 0 else Decimal("0.00")
    due = grand - applied
    return due if due > Decimal("0.00") else Decimal("0.00")


def get_customer_payment_info(customer_id: int) -> dict:
    """
    Balance + open dues for payment UI:
    unpaid / partial sales (with invoice item particulars) and loans.
    """
    customer = Customer.query.filter_by(id=customer_id, is_deleted=False).first()
    if not customer:
        raise ValueError("Customer not found.")

    balance = _money(customer.balance)
    owes = balance if balance > 0 else Decimal("0.00")
    advance = (-balance) if balance < 0 else Decimal("0.00")

    entries: list[dict] = []

    sales = (
        Sale.query.options(joinedload(Sale.items).joinedload(SaleItem.product))
        .filter(
            Sale.customer_id == customer.id,
            Sale.payment_status.in_((PaymentStatus.UNPAID, PaymentStatus.PARTIAL)),
        )
        .order_by(Sale.sale_date.desc(), Sale.id.desc())
        .all()
    )
    for sale in sales:
        due = _sale_due(sale)
        if due <= 0:
            continue
        status = "Partial" if sale.payment_status == PaymentStatus.PARTIAL else "Unpaid"
        invoice = (sale.invoice_no or f"Sale #{sale.id}").strip()
        # Same style as customer ledger: product qty lines
        item_parts = items_particulars(sale.items or [], fallback="", kind="sale") or ""
        item_parts = item_parts.strip()
        # Full line for remarks / tooltip: Sale INV-… / product particulars
        full_particulars = _sale_particulars_notes(sale)
        entries.append(
            {
                "id": f"sale-{sale.id}",
                "kind": "sale",
                "ref_id": sale.id,
                "date": sale.sale_date.isoformat() if sale.sale_date else "",
                "label": invoice,
                "particulars": item_parts or (sale.notes or "").strip() or "Sale",
                "remark": full_particulars,
                "status": status,
                "amount": float(due),
            }
        )

    loans = (
        CustomerReceiving.query.filter_by(customer_id=customer.id, payment_type="loan")
        .order_by(CustomerReceiving.receiving_date.desc(), CustomerReceiving.id.desc())
        .all()
    )
    for loan in loans:
        amt = _money(loan.amount)
        if amt <= 0:
            continue
        note = (loan.notes or "").strip() or "Cash loan to customer"
        label = f"Loan #{loan.id}"
        entries.append(
            {
                "id": f"loan-{loan.id}",
                "kind": "loan",
                "ref_id": loan.id,
                "date": loan.receiving_date.isoformat() if loan.receiving_date else "",
                "label": label,
                "particulars": note,
                "remark": f"Loan #{loan.id} / {note}",
                "status": "Loan",
                "amount": float(amt),
            }
        )

    entries_total = sum((Decimal(str(e["amount"])) for e in entries), Decimal("0.00"))

    return {
        "ok": True,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "balance": float(balance),
        "owes": float(owes),
        "advance": float(advance),
        "entries": entries,
        "entries_total": float(entries_total.quantize(Decimal("0.01"))),
    }
