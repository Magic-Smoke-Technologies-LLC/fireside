"""The monthly grant a paid plan carries.

A subscriber should not have to buy credit to hear the first book, so each paid
period adds `quota.PLAN_GRANT[plan]` dollars to the wallet (characters, for an
account that still holds characters - the unit follows the account, never the
plan). Two properties matter:

  * it is granted once per period, keyed on `current_period_end` in the ledger
    id, so a page reload or a retried request cannot stack it;
  * it is granted only while the subscription is genuinely active. An expired
    row (see billing.active) carries no allowance, whatever status says.

The grant is a top-up, not an allowance that expires: it lands in the balance
and rolls, so a reader who skips a month does not lose the money.
"""

from host import accounting, billing, quota


def _entry_id(user_id, period_end):
    return "allowance:%s:%s" % (user_id, int(period_end or 0))


def due(conn, user_id):
    """Grant this period's allowance if it is owed. Returns the amount added in
    the account's own unit (0 when nothing was owed). Safe to call as often as
    you like."""
    if not user_id:
        return 0
    sub = billing.get(conn, user_id)
    if not sub:
        return 0
    plan = sub.get("plan") or ""
    # The unit is the account's, not the plan's: a wallet already held in
    # characters keeps the character ladder, a dollar wallet gets dollars.
    unit = accounting.currency(conn, user_id) or "USD"
    if unit == "chars":
        amount, cur = float(quota.PLAN_CHARS.get(plan, 0)), "chars"
    else:
        amount, cur = float(quota.PLAN_GRANT.get(plan, 0.0)), "USD"
    if amount <= 0:                     # free tier, or a plan that includes none
        return 0
    if not billing.active(conn, user_id):
        return 0
    period_end = sub.get("current_period_end") or 0
    eid = _entry_id(user_id, period_end)
    if conn.execute("SELECT id FROM ledger WHERE id=?", (eid,)).fetchone():
        return 0
    try:
        accounting.topup(conn, user_id, amount,
                         currency=cur, entry_id=eid,
                         note="included with your plan, period ending %s" % period_end)
    except Exception:
        # A concurrent request won the same primary key; the ledger already has
        # this period's row, so this caller owes nothing.
        return 0
    return amount
