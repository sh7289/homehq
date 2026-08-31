from datetime import date, timedelta

DEFAULT_WARNING_DAYS = 30

# Fallback "general use by" windows, used only when an item has an
# acquired_date but no recorded expiry_date. Overridable per item via
# shelf_life_days.
DEFAULT_SHELF_LIFE_DAYS = {
    "pantry": 1095,  # ~3 years, typical dry-goods shelf life
    "freezer": 365,  # ~12 months, general freezer use-by guideline
}


def classify(expiry_date_str, today=None, warning_days=DEFAULT_WARNING_DAYS):
    """Return "expired", "expiring_soon", or None for a date string."""
    if not expiry_date_str:
        return None
    try:
        expiry = date.fromisoformat(expiry_date_str)
    except ValueError:
        return None

    today = today or date.today()
    if expiry < today:
        return "expired"
    if expiry <= today + timedelta(days=warning_days):
        return "expiring_soon"
    return None


def effective_expiry(item, storage=None):
    """Return (date_str, estimated) -- the date to use for alerts.

    Prefers a recorded expiry_date. Falls back to acquired_date plus a
    shelf-life window (per-item override, else the storage-type default)
    when no expiry_date is recorded but an acquired_date is.
    """
    if item.get("expiry_date"):
        return item["expiry_date"], False

    acquired = item.get("acquired_date")
    if not acquired:
        return None, False

    try:
        acquired_date = date.fromisoformat(acquired)
    except ValueError:
        return None, False

    shelf_life_days = item.get("shelf_life_days") or DEFAULT_SHELF_LIFE_DAYS.get(
        storage, DEFAULT_SHELF_LIFE_DAYS["pantry"]
    )
    return (acquired_date + timedelta(days=shelf_life_days)).isoformat(), True


def split_by_expiry(items, storage=None, today=None, warning_days=DEFAULT_WARNING_DAYS):
    """Split items into (expired, expiring_soon), each sorted soonest-first.

    Mutates each item dict in place, adding effective_expiry,
    effective_expiry_estimated, and expiry_status.
    """
    expired = []
    expiring_soon = []
    for item in items:
        eff_date, estimated = effective_expiry(item, storage=storage)
        status = classify(eff_date, today=today, warning_days=warning_days)
        item["effective_expiry"] = eff_date
        item["effective_expiry_estimated"] = estimated
        item["expiry_status"] = status
        if status == "expired":
            expired.append(item)
        elif status == "expiring_soon":
            expiring_soon.append(item)

    expired.sort(key=lambda i: i["effective_expiry"])
    expiring_soon.sort(key=lambda i: i["effective_expiry"])
    return expired, expiring_soon
