"""The platform tier's monthly character allowance.

A subscriber should not have to buy credits to hear the first book, so the tier
carries `quota.PLATFORM_MONTH_CHARS` per paid period. Two properties matter:

  * it is granted once per period, keyed on `current_period_end` in the ledger
    id, so a page reload or a retried request cannot stack it;
  * it is granted only while the subscription is genuinely active. An expired
    row (see billing.active) carries no allowance, whatever status says.
"""

from host import accounting, billing, quota


def _entry_id(user_id, period_end):
    return "allowance:%s:%s" % (user_id, int(period_end or 0))


def due(conn, user_id):
    """Grant this period's allowance if it is owed. Returns chars added (0 when
    nothing was owed). Safe to call as often as you like."""
    if not user_id or quota.PLATFORM_MONTH_CHARS <= 0:
        return 0
    sub = billing.get(conn, user_id)
    if not sub or sub.get("plan") != quota.PLATFORM_PLAN:
        return 0
    if not billing.active(conn, user_id):
        return 0
    period_end = sub.get("current_period_end") or 0
    eid = _entry_id(user_id, period_end)
    if conn.execute("SELECT id FROM ledger WHERE id=?", (eid,)).fetchone():
        return 0
    try:
        accounting.topup(conn, user_id, quota.PLATFORM_MONTH_CHARS,
                         currency="chars", entry_id=eid,
                         note="platform allowance for period ending %s" % period_end)
    except Exception:
        # A concurrent request won the same primary key; the ledger already has
        # this period's row, so this caller owes nothing.
        return 0
    return quota.PLATFORM_MONTH_CHARS
