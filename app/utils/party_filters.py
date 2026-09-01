"""Active/inactive filters for customer and vendor lists and lookups."""

PARTY_ACTIVE_FILTERS = ("active", "inactive", "all")


def parse_party_active(value, default="active"):
    v = (value or default).strip().lower()
    return v if v in PARTY_ACTIVE_FILTERS else default


def apply_party_active_filter(query, model, active_filter):
    if active_filter == "active":
        return query.filter(model.is_active.is_(True))
    if active_filter == "inactive":
        return query.filter(model.is_active.is_(False))
    return query
