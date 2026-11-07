"""Recurring platform subscriptions, kept on the server.

The platform tier is separate from character capacity: it pays for the server and
storage and buys nothing else (docs/PRICING.md). The host holds only the state the
payment provider reported - it never computes a price or a period. One active
subscription per user for now; the provider is the authority on status.

Add-ons are separate rows: a storage block is a monthly thing you buy N of, and
it adds bytes to whichever tier you are on instead of replacing it. The provider
is still the authority on status and period; this module holds the count.
"""

import time

from host.db import now

STORAGE_ADDON = "storage"

# How long past its paid period a row stays "active" before the host decides the
# provider has stopped telling it anything. Webhooks are the authority, but a
# webhook that never arrives (an endpoint deleted, a delivery outage, a plan
# cancelled from the Stripe dashboard) must not leave a paid tier running
# forever. Three days is long enough to survive a delivery outage and short
# enough that a lapsed charge does not buy a whole extra month.
LAPSE_GRACE_SECONDS = 3 * 86400


def get(conn, uid):
    r = conn.execute("SELECT * FROM subscriptions WHERE user_id=?",
                     (uid,)).fetchone()
    if not r:
        return None
    return {"user_id": r["user_id"], "plan": r["plan"], "status": r["status"],
            "provider": r["provider"], "reference": r["reference"],
            "current_period_end": r["current_period_end"],
            "customer": r["customer"] or "",
            "subscription_id": r["subscription_id"] or "",
            "updated_at": r["updated_at"]}


def upsert(conn, uid, plan, status, provider="", reference="",
           current_period_end=0, customer="", subscription_id=""):
    """Record what the provider said. Merges into the one row a user has.

    A lifecycle event often knows the status but not the checkout session, and a
    checkout knows both. An empty value therefore never overwrites a stored one:
    `excluded` is only taken when it is non-empty, so a renewal cannot blank the
    ids that make a later cancel possible.
    """
    conn.execute(
        "INSERT INTO subscriptions (user_id, plan, status, provider, reference, "
        "current_period_end, customer, subscription_id, updated_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, "
        "status=excluded.status, provider=excluded.provider, "
        "reference=COALESCE(NULLIF(excluded.reference, ''), subscriptions.reference), "
        "current_period_end=excluded.current_period_end, "
        "customer=COALESCE(NULLIF(excluded.customer, ''), subscriptions.customer), "
        "subscription_id=COALESCE(NULLIF(excluded.subscription_id, ''), "
        "subscriptions.subscription_id), "
        "updated_at=excluded.updated_at",
        (uid, plan, status, provider, reference, int(current_period_end or 0),
         customer or "", subscription_id or "", now(), now()))
    conn.commit()
    return get(conn, uid)


def by_subscription(conn, subscription_id):
    """The account a provider subscription belongs to, or "".

    A renewal or a failed payment names the subscription, never our user id, so
    this is the only way back to a row. Checked against both tables because a
    cart's storage add-on is written on its own row.
    """
    sid = (subscription_id or "").strip()
    if not sid:
        return ""
    for table in ("subscriptions", "addons"):
        r = conn.execute("SELECT user_id FROM %s WHERE subscription_id=?" % table,
                         (sid,)).fetchone()
        if r:
            return r["user_id"]
    return ""


def _lapsed(period_end, now_ts=None):
    """True when a paid period ended long enough ago that the row is stale."""
    try:
        end = int(period_end or 0)
    except (TypeError, ValueError):
        return False
    if end <= 0:
        # no period recorded (the free tier, or a grant): nothing to expire.
        return False
    ts = time.time() if now_ts is None else now_ts
    return ts > end + LAPSE_GRACE_SECONDS


def active(conn, uid, now_ts=None):
    """True when the user's subscription is currently paid/active.

    Status alone is not enough: `current_period_end` is what the provider's
    renewal would have moved, and nothing renews if the webhook never lands. A
    row whose period ended more than the grace window ago is not active, whatever
    status it still carries.
    """
    s = get(conn, uid)
    if not s or s["status"] not in ("active", "trialing"):
        return False
    return not _lapsed(s["current_period_end"], now_ts)


def assign_free(conn, uid):
    """Put a new account on the free tier, once.

    Called when a user row is first created so sign-ups start at 500 MB. An
    account that already has any subscription row is left alone, and a missing
    row keeps failing open to the platform allowance (quota.allowance_bytes), so
    accounts that predate the free tier are not silently cut down to 500 MB.
    """
    if get(conn, uid) is not None:
        return get(conn, uid)
    return upsert(conn, uid, "free", "active", provider="", reference="")


def addons(conn, uid):
    """{addon: row} for the user's add-ons."""
    out = {}
    for r in conn.execute("SELECT * FROM addons WHERE user_id=?", (uid,)):
        out[r["addon"]] = {"addon": r["addon"], "quantity": r["quantity"],
                           "status": r["status"], "provider": r["provider"],
                           "reference": r["reference"],
                           "current_period_end": r["current_period_end"],
                           "customer": r["customer"] or "",
                           "subscription_id": r["subscription_id"] or ""}
    return out


def upsert_addon(conn, uid, addon, quantity, status, provider="", reference="",
                 current_period_end=0, customer="", subscription_id=""):
    """Record how many of an add-on the provider says are paid for."""
    conn.execute(
        "INSERT INTO addons (user_id, addon, quantity, status, provider, "
        "reference, current_period_end, customer, subscription_id, updated_at, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id, addon) DO UPDATE SET quantity=excluded.quantity, "
        "status=excluded.status, provider=excluded.provider, "
        "reference=COALESCE(NULLIF(excluded.reference, ''), addons.reference), "
        "current_period_end=excluded.current_period_end, "
        "customer=COALESCE(NULLIF(excluded.customer, ''), addons.customer), "
        "subscription_id=COALESCE(NULLIF(excluded.subscription_id, ''), "
        "addons.subscription_id), "
        "updated_at=excluded.updated_at",
        (uid, addon, int(quantity or 0), status, provider, reference,
         int(current_period_end or 0), customer or "", subscription_id or "",
         now(), now()))
    conn.commit()
    return addons(conn, uid).get(addon)


def blocks(conn, uid):
    """Paid storage blocks the account currently holds (an active add-on)."""
    a = addons(conn, uid).get(STORAGE_ADDON)
    if not a or a["status"] not in ("active", "trialing"):
        return 0
    if _lapsed(a["current_period_end"]):
        return 0
    try:
        return max(0, int(a["quantity"] or 0))
    except (TypeError, ValueError):
        return 0


def list_all(conn):
    """{user_id: row} for every account with a subscription row. An account
    with no row is on no tier here and fails open to the platform allowance."""
    out = {}
    for r in conn.execute("SELECT * FROM subscriptions"):
        out[r["user_id"]] = {"user_id": r["user_id"], "plan": r["plan"],
                             "status": r["status"], "provider": r["provider"],
                             "reference": r["reference"],
                             "current_period_end": r["current_period_end"],
                             "customer": r["customer"] or "",
                             "subscription_id": r["subscription_id"] or ""}
    return out


# ---- Paddle mirror (2026-11-07) --------------------------------------------
# The provider's word about who bought what, kept next to the row the host reads.
#
# Why a mirror at all: Paddle checkout happens in the page with no server round
# trip, so when the first webhook arrives for a new buyer there is no row naming
# them. The mirror is what turns a Paddle customer id (and the account id we put
# in custom_data) into a user, once, and keeps it.
#
# Two layers, one owner each:
#
#   paddle_customers / paddle_subscriptions  the mirror: Paddle's own ids, as
#       delivered. The audit trail, and the only place a customer id is looked
#       up from a user id.
#   subscriptions                            the projection: one row per user,
#       the tier the rest of the host already reads (quota, allowance, plans).
#       Written only from the mirror, through upsert().
#
# Deliveries are at-least-once and may arrive out of order, so:
#   * an event id already applied is acknowledged and skipped;
#   * a delivery older than the row it would overwrite is acknowledged and
#     skipped, per subscription, on occurred_at.
#
# Access rules, in one place: the projector writes the provider's status into
# the projection, and billing.active() stays the single decision (active or
# trialing, and not lapsed past its paid period). So:
#   active, trialing   -> access
#   past_due, paused,
#   canceled, expired  -> no access
# A scheduled_change is not a status: Paddle leaves the subscription active until
# the change takes effect, so a pending cancel never revokes anything here.

GRANTING_STATUSES = ("active", "trialing")


def grants_access(status):
    """True when a subscription status should let the account listen.

    Kept identical to active()'s status test on purpose: two helpers that
    disagree about who is paid are a bug waiting for a quiet evening.
    """
    return str(status or "").strip().lower() in GRANTING_STATUSES


def customer_for(conn, uid):
    """The Paddle customer linked to this user, or None."""
    if not uid:
        return None
    r = conn.execute(
        "SELECT * FROM paddle_customers WHERE user_id=? "
        "ORDER BY updated_at DESC LIMIT 1", (uid,)).fetchone()
    if not r:
        return None
    return {"customer_id": r["customer_id"], "user_id": r["user_id"],
            "email": r["email"], "updated_at": r["updated_at"]}


def customer_by_id(conn, customer_id):
    r = conn.execute("SELECT * FROM paddle_customers WHERE customer_id=?",
                     (customer_id or "",)).fetchone()
    if not r:
        return None
    return {"customer_id": r["customer_id"], "user_id": r["user_id"],
            "email": r["email"], "updated_at": r["updated_at"]}


def _user_by_email(conn, email):
    """The account an email belongs to, if we have one. Both sign-up paths are
    checked: a local account and a linked identity. Never creates a user."""
    e = (email or "").strip()
    if not e:
        return ""
    r = conn.execute(
        "SELECT user_id FROM local_accounts WHERE LOWER(email)=LOWER(?) LIMIT 1",
        (e,)).fetchone()
    if r and r["user_id"]:
        return r["user_id"]
    r = conn.execute(
        "SELECT user_id FROM identities WHERE LOWER(email)=LOWER(?) LIMIT 1",
        (e,)).fetchone()
    return (r["user_id"] if r and r["user_id"] else "")


def link_customer(conn, customer_id, email="", user_id=""):
    """Remember which account a Paddle customer is, without erasing what we know.

    A later event that omits the email or the metadata must not blank a known
    link, so this reads first and writes after rather than upserting blind.
    """
    cid = (customer_id or "").strip()
    if not cid:
        return None
    row = customer_by_id(conn, cid)
    uid = (user_id or "").strip()
    mail = (email or "").strip()
    if row:
        if not uid:
            uid = row["user_id"] or _user_by_email(conn, mail or row["email"])
        if not mail:
            mail = row["email"] or ""
    elif not uid:
        uid = _user_by_email(conn, mail)
    conn.execute(
        "INSERT INTO paddle_customers (customer_id, user_id, email, updated_at) "
        "VALUES (?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET "
        "user_id=excluded.user_id, email=excluded.email, "
        "updated_at=excluded.updated_at",
        (cid, uid, mail, now()))
    conn.commit()
    return customer_by_id(conn, cid)


def subscription_rows(conn, uid):
    """Every Paddle subscription mirrored for this account, newest first."""
    out = []
    for r in conn.execute(
            "SELECT * FROM paddle_subscriptions WHERE user_id=? "
            "ORDER BY occurred_at DESC", (uid,)):
        out.append(dict(r))
    return out


def mirror_subscription(conn, sub):
    """Upsert one subscription from a verified event, then re-project the tier.

    Returns the stored row, or None when the event names no subscription.
    """
    sub = sub or {}
    sid = (sub.get("subscription_id") or "").strip()
    if not sid:
        return None
    cur = conn.execute("SELECT * FROM paddle_subscriptions WHERE subscription_id=?",
                       (sid,)).fetchone()
    occurred = sub.get("occurred_at") or ""
    if cur and occurred and (cur["occurred_at"] or "") and occurred < cur["occurred_at"]:
        return dict(cur)                     # a late, older delivery: keep the newer row
    uid = (sub.get("user_id") or "").strip()
    if not uid and sub.get("customer_id"):
        c = customer_by_id(conn, sub["customer_id"])
        uid = (c or {}).get("user_id") or ""
        if not uid:
            uid = _user_by_email(conn, (c or {}).get("email") or "")
    if not uid and cur:
        uid = cur["user_id"] or ""
    conn.execute(
        "INSERT INTO paddle_subscriptions (subscription_id, customer_id, user_id, "
        "status, price_id, product_id, plan, scheduled_change_action, "
        "scheduled_change_at, current_period_end, occurred_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(subscription_id) DO UPDATE SET "
        "customer_id=excluded.customer_id, user_id=excluded.user_id, "
        "status=excluded.status, price_id=excluded.price_id, "
        "product_id=excluded.product_id, plan=excluded.plan, "
        "scheduled_change_action=excluded.scheduled_change_action, "
        "scheduled_change_at=excluded.scheduled_change_at, "
        "current_period_end=excluded.current_period_end, "
        "occurred_at=excluded.occurred_at, updated_at=excluded.updated_at",
        (sid, sub.get("customer_id") or "", uid, sub.get("status") or "",
         sub.get("price_id") or "", sub.get("product_id") or "",
         sub.get("plan") or "", sub.get("scheduled_change_action") or "",
         int(sub.get("scheduled_change_at") or 0),
         int(sub.get("current_period_end") or 0), occurred, now(), now()))
    conn.commit()
    if uid:
        project(conn, uid)
    row = conn.execute("SELECT * FROM paddle_subscriptions WHERE subscription_id=?",
                       (sid,)).fetchone()
    return dict(row) if row else None


def project(conn, uid):
    """Write this account's tier from its mirrored subscriptions.

    The deciding row is the granting subscription with the furthest period end,
    so an upgrade wins over the rung it replaced. With nothing granting, the most
    recent delivery decides, which is how a cancel takes access away. A price we
    do not recognize leaves the tier untouched instead of guessing.
    """
    rows = subscription_rows(conn, uid)
    if not rows:
        return get(conn, uid)
    granting = [r for r in rows if grants_access(r["status"])]
    if granting:
        pick = max(granting, key=lambda r: (int(r["current_period_end"] or 0),
                                            r["occurred_at"] or ""))
    else:
        pick = max(rows, key=lambda r: (r["occurred_at"] or "",
                                        int(r["current_period_end"] or 0)))
    if not (pick.get("plan") or "").strip():
        return get(conn, uid)
    current = get(conn, uid)
    if current and current.get("provider") == "admin" and current.get("status") in ("active", "trialing"):
        return current                       # a hand-granted tier outranks a sale
    return upsert(conn, uid, pick["plan"], pick["status"], provider="paddle",
                  reference=pick["subscription_id"],
                  current_period_end=pick["current_period_end"] or 0,
                  customer=pick.get("customer_id") or "",
                  subscription_id=pick["subscription_id"])


def apply_event(conn, event):
    """Apply one normalized, already-verified event. Idempotent by event id.

    The provider verified the signature; this function only records what it said
    and lets the projection decide access. Unknown kinds are acknowledged so the
    sender stops retrying them.
    """
    ev = event or {}
    eid = (ev.get("event_id") or "").strip()
    etype = ev.get("event_type") or ""
    kind = ev.get("kind") or "ignore"
    out = {"ok": True, "event": etype, "kind": kind, "applied": False}
    if eid:
        seen = conn.execute("SELECT event_id FROM paddle_events WHERE event_id=?",
                            (eid,)).fetchone()
        if seen:
            out["detail"] = "already applied"
            return out
        conn.execute(
            "INSERT INTO paddle_events (event_id, event_type, occurred_at, kind, "
            "received_at) VALUES (?,?,?,?,?)",
            (eid, etype, ev.get("occurred_at") or "", kind, now()))
        conn.commit()
    cust = ev.get("customer") or None
    if cust and (cust.get("customer_id") or "").strip():
        out["customer"] = link_customer(conn, cust["customer_id"],
                                        cust.get("email") or "",
                                        cust.get("user_id") or "")
    if kind == "subscription" and ev.get("subscription"):
        sub = dict(ev["subscription"])
        if not sub.get("occurred_at"):
            sub["occurred_at"] = ev.get("occurred_at") or ""
        row = mirror_subscription(conn, sub)
        out["applied"] = bool(row)
        out["subscription"] = row
        if row and row.get("user_id"):
            out["user"] = row["user_id"]
    elif kind == "customer":
        out["applied"] = bool(out.get("customer"))
    elif kind == "transaction":
        out["transaction"] = ev.get("transaction") or {}
        out["applied"] = bool(out["transaction"])
    else:
        out["detail"] = "ignored event type"
    return out
