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

import time

from host import accounting, billing, quota


def _entry_id(user_id, period_end):
    return "allowance:%s:%s" % (user_id, int(period_end or 0))


def _note(period_end):
    """What the wallet row says. Never print a raw epoch, and never print a
    zero: a subscription with no period end is a comp or a sandbox row, and
    "period ending 0" is a number the reader cannot do anything with. Chris,
    2026-11-10, read exactly that on his own wallet page and did not know what
    the row was."""
    try:
        ts = int(float(period_end or 0))
    except (TypeError, ValueError):
        ts = 0
    if ts <= 0:
        return "included with your plan"
    return "included with your plan, this period ends %s" % time.strftime(
        "%d %b %Y", time.gmtime(ts))


def welcome(conn, user_id):
    """The $3 a new subscriber starts with. Once per account, ever.

    Nothing lands at sign-up. The free tier is free, and credit that arrives
    before any money does is a thing to re-harvest with throwaway accounts. It
    lands on the first period that has actually been charged, counted by status
    "active" rather than by billing.active() (which also answers yes during a
    trial). The ledger id carries the user and not the period, so a cancel and
    a re-subscribe cannot collect it a second time.

    Returns the amount added, 0 when nothing was owed.
    """
    if not user_id:
        return 0.0
    sub = billing.get(conn, user_id)
    if not sub:
        return 0.0
    if (sub.get("plan") or "") == quota.FREE_PLAN:
        return 0.0
    if str(sub.get("status") or "") != "active":
        return 0.0
    # A legacy character wallet is an account from before the switch, and it is
    # long past its first paid month. Never mix the two units.
    if (accounting.currency(conn, user_id) or "USD") != "USD":
        return 0.0
    eid = "welcome:%s" % user_id
    if conn.execute("SELECT id FROM ledger WHERE id=?", (eid,)).fetchone():
        return 0.0
    amount = float(quota.WELCOME_GRANT)
    if amount <= 0:
        return 0.0
    try:
        accounting.topup(conn, user_id, amount, currency="USD", entry_id=eid,
                         note="welcome credit, once per account")
    except Exception:
        # A concurrent request won the same primary key; the ledger has it.
        return 0.0
    return amount


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
                         note=_note(period_end))
    except Exception:
        # A concurrent request won the same primary key; the ledger already has
        # this period's row, so this caller owes nothing.
        return 0
    return amount
