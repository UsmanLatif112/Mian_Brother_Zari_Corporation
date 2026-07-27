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
    return bal


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
