from decimal import Decimal

from app.extensions import db
from app.models import LedgerEntry
from app.models.mixins import utcnow


def _last_balance(party_type: str, party_id: int) -> Decimal:
    last = (
        LedgerEntry.query.filter_by(party_type=party_type, party_id=party_id)
        .order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc())
        .first()
    )
    return last.balance_after if last else Decimal("0")


def post_ledger_entry(
    party_type,
    party_id,
    entry_type,
    debit=Decimal("0"),
    credit=Decimal("0"),
    entry_date=None,
    reference_type=None,
    reference_id=None,
    notes=None,
):
    from app.utils.working_date import get_working_date

    debit = Decimal(str(debit))
    credit = Decimal(str(credit))
    balance = _last_balance(party_type, party_id) + debit - credit
    entry = LedgerEntry(
        party_type=party_type,
        party_id=party_id,
        entry_date=entry_date or get_working_date(),
        entry_type=entry_type,
        reference_type=reference_type,
        reference_id=reference_id,
        debit=debit,
        credit=credit,
        balance_after=balance,
        notes=notes,
        created_at=utcnow(),
    )
    db.session.add(entry)
    return entry, balance


def rebuild_party_balances(party_type, party_id, sync_party=True):
    """Recompute balance_after for all ledger rows; optionally sync Customer/Vendor.balance."""
    entries = (
        LedgerEntry.query.filter_by(party_type=party_type, party_id=party_id)
        .order_by(LedgerEntry.entry_date.asc(), LedgerEntry.id.asc())
        .all()
    )
    bal = Decimal("0")
    for entry in entries:
        bal = bal + Decimal(str(entry.debit or 0)) - Decimal(str(entry.credit or 0))
        entry.balance_after = bal

    if sync_party:
        if party_type == "customer":
            from app.models import Customer

            party = db.session.get(Customer, party_id)
            if party:
                party.balance = bal
        elif party_type == "vendor":
            from app.models import Vendor

            party = db.session.get(Vendor, party_id)
            if party:
                party.balance = bal
        elif party_type == "salesman":
            from app.models import Salesman

            party = db.session.get(Salesman, party_id)
            if party:
                party.balance = bal
    return bal


def sync_party_opening_entry(
    party_type,
    party_id,
    opening_balance,
    entry_date=None,
    notes=None,
):
    """
    Keep a single entry_type=opening ledger row in sync with opening_balance.

    Total party.balance is then rebuilt as: opening + later in/out (debit − credit).
    When opening is 0, any opening row is removed.
    """
    opening = Decimal(str(opening_balance or 0))
    existing = (
        LedgerEntry.query.filter_by(
            party_type=party_type, party_id=party_id, entry_type="opening"
        )
        .order_by(LedgerEntry.id.asc())
        .all()
    )
    primary = existing[0] if existing else None
    for dup in existing[1:]:
        db.session.delete(dup)

    if opening == 0:
        if primary:
            db.session.delete(primary)
            db.session.flush()
        rebuild_party_balances(party_type, party_id)
        return None

    debit = opening if opening > 0 else Decimal("0")
    credit = abs(opening) if opening < 0 else Decimal("0")

    if primary:
        primary.debit = debit
        primary.credit = credit
        if entry_date is not None:
            primary.entry_date = entry_date
        if notes is not None:
            primary.notes = notes
        entry = primary
    else:
        from app.utils.working_date import get_working_date

        entry = LedgerEntry(
            party_type=party_type,
            party_id=party_id,
            entry_date=entry_date or get_working_date(),
            entry_type="opening",
            debit=debit,
            credit=credit,
            balance_after=opening,
            notes=notes,
            created_at=utcnow(),
        )
        db.session.add(entry)

    db.session.flush()
    rebuild_party_balances(party_type, party_id)
    return entry


def update_ledger_entry(entry_id, entry_date=None, debit=None, credit=None, notes=None, entry_type=None):
    entry = db.session.get(LedgerEntry, entry_id)
    if not entry:
        raise ValueError("Ledger entry not found.")
    if entry_date is not None:
        if isinstance(entry_date, str):
            from datetime import date as date_cls

            entry.entry_date = date_cls.fromisoformat(entry_date)
        else:
            entry.entry_date = entry_date
    if debit is not None:
        entry.debit = Decimal(str(debit or 0))
    if credit is not None:
        entry.credit = Decimal(str(credit or 0))
    if notes is not None:
        entry.notes = (notes or "").strip() or None
    if entry_type is not None:
        entry.entry_type = entry_type
    rebuild_party_balances(entry.party_type, entry.party_id)
    return entry


def delete_ledger_entry(entry_id):
    entry = db.session.get(LedgerEntry, entry_id)
    if not entry:
        raise ValueError("Ledger entry not found.")
    party_type, party_id = entry.party_type, entry.party_id
    db.session.delete(entry)
    db.session.flush()
    rebuild_party_balances(party_type, party_id)
    return party_type, party_id


def delete_ledger_entry_cascading(entry_id, user_id=None):
    """
    Delete a ledger row by reversing its source document when possible
    (sale, purchase, customer/vendor payment), so stock, cash, and stats
    stay consistent. Manual/opening rows fall back to ledger-only delete.
    """
    entry = db.session.get(LedgerEntry, entry_id)
    if not entry:
        raise ValueError("Ledger entry not found.")

    party_type, party_id = entry.party_type, entry.party_id
    ref_type = (entry.reference_type or "").strip().lower()
    ref_id = entry.reference_id

    if ref_type == "sale" and ref_id:
        from app.services.sale_service import void_sale

        void_sale(int(ref_id), user_id)
        return party_type, party_id, "sale"

    if ref_type == "customer_receiving" and ref_id:
        from app.services.customer_payment_service import delete_customer_payment

        delete_customer_payment(int(ref_id), user_id)
        return party_type, party_id, "customer_receiving"

    if ref_type == "vendor_payment" and ref_id:
        from app.services.vendor_payment_service import delete_vendor_payment

        delete_vendor_payment(int(ref_id), user_id)
        return party_type, party_id, "vendor_payment"

    if ref_type == "purchase" and ref_id:
        from app.services.purchase_service import void_purchase

        void_purchase(int(ref_id), user_id)
        return party_type, party_id, "purchase"

    delete_ledger_entry(entry_id)
    return party_type, party_id, "ledger"


def delete_ledger_by_reference(reference_type, reference_id, rebuild=True):
    rows = LedgerEntry.query.filter_by(
        reference_type=reference_type, reference_id=reference_id
    ).all()
    parties = set()
    for row in rows:
        parties.add((row.party_type, row.party_id))
        db.session.delete(row)
    db.session.flush()
    if rebuild:
        for party_type, party_id in parties:
            rebuild_party_balances(party_type, party_id, sync_party=False)
    return parties
