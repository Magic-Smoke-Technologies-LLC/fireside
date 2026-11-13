"""Paddle Billing: the card form is Paddle's, the fulfillment is ours.

Two halves, and this module is the seam between them.

  * The price ids and the overlay live in the page (Paddle.js), so we never
    create a transaction by hand and never invent a number Paddle will charge.
    That means no row exists on our side when the money moves, which is why the
    first webhook for a new buyer has to be able to find its account on its own.
  * Everything after that arrives as a signed webhook. This module verifies the
    signature and normalizes each delivery into one shape the core mirrors
    (host.billing.apply_event). It writes no rows itself: the mirror lives in
    host/billing.py, next to the row the rest of the host already reads.

Environment, all server-side. None of it is read by the browser:

  FIRESIDE_PADDLE_API_KEY           pdl_sdbx_apikey_... (sandbox) or pdl_live_apikey_...
  FIRESIDE_PADDLE_WEBHOOK_SECRET    the notification destination's SECRET KEY.
                                    This is not the API key. Shown under
                                    Developer tools > Notifications.
  FIRESIDE_PADDLE_ENV              "sandbox" (default) or "production"
  FIRESIDE_PADDLE_PRICES           optional JSON {price_id: plan}. The defaults
                                    below are the sandbox catalog read back on
                                    2026-11-06; production sets this.
  FIRESIDE_PADDLE_SIGNATURE_TOLERANCE  seconds, default 5. 0 disables the replay
                                    window, and only a wrong clock justifies it.

Signature verification follows the documented algorithm rather than pulling the
Paddle SDK into the container: Paddle-Signature is "ts=<unix>;h1=<hex>", and h1
is HMAC-SHA256(secret, "<ts>:<raw body>"). The raw bytes are what matter, which
is why the webhook route hands this function the body before anything parses it.
A body that has been parsed and re-serialized will not match.
"""

import datetime
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request

from host.payments import Checkout, Plan

ID = "paddle"

SANDBOX_API = "https://sandbox-api.paddle.com"
LIVE_API = "https://api.paddle.com"

DEFAULT_TOLERANCE = 5          # seconds, the window the Paddle SDKs enforce
PRICE_CACHE_SECONDS = 300      # how long a read catalog is reused (see plans())

# Our own key in Paddle's custom_data, so an event can find the account it
# belongs to even when the buyer paid with a different email than they signed up
# with. The page sets it at checkout.
METADATA_KEY = "fireside_user"

# The add-on id, kept here as a literal rather than imported from host.quota so
# this module stays a provider adapter: what it must agree with is the price
# id, not the rest of the host.
STORAGE_ADDON = "storage"

# price id -> tier id. Both cycles of a rung map to the same tier; the interval
# changes when the period ends, never what the buyer is entitled to.
# The amounts must match host/plans.py LIST_PRICE_CENTS, because the page prints
# those and this table is what the overlay actually charges. They did not: the
# monthly ids still pointed at the first sandbox catalog ($3/$8/$20) while the
# page printed $5/$10/$22, so a reader saw $5 and the card was asked for $3.
# Found 2026-11-13 by opening the sandbox checkout beside the page.
# tools/check_catalog.py is the receipt: it reads these ids back from Paddle.
DEFAULT_PRICES = {
    "pri_01m719e8h2jm71bcbks6kb5w7k": "platform",    # Reader 5.00 monthly
    "pri_01m6szmwx3174hsp6qe85yest5": "platform",    # Reader 36.00 annual
    "pri_01m719e8jdz89r9dkc3bvcthec": "listener",    # Listener 10.00 monthly
    "pri_01m6szmx4a3cpnx4x8nt9g0gbr": "listener",    # Listener 96.00 annual
    "pri_01m719e8kvz5jqxdbjx3g1785n": "studio",      # Studio 22.00 monthly
    "pri_01m6szmx9pv1wvncna1twjxaj7": "studio",      # Studio 240.00 annual
}

_SIG_FIELD = re.compile(r"([A-Za-z0-9_]+)=([^;]+)")

# The same catalog read the other way: tier -> {"month"/"year": price id}. The
# overlay needs both cycles per rung, because the page sells a cycle and the
# webhook only reports one. Two tables that describe one catalog can drift, so
# tests/test_paddle.py asserts every id here appears in DEFAULT_PRICES.
DEFAULT_PLAN_PRICES = {
    "platform": {"month": "pri_01m719e8h2jm71bcbks6kb5w7k",
                 "year": "pri_01m6szmwx3174hsp6qe85yest5"},
    "listener": {"month": "pri_01m719e8jdz89r9dkc3bvcthec",
                 "year": "pri_01m6szmx4a3cpnx4x8nt9g0gbr"},
    "studio": {"month": "pri_01m719e8kvz5jqxdbjx3g1785n",
               "year": "pri_01m6szmx9pv1wvncna1twjxaj7"},
}

# The storage block and the wallet top-ups are the other two things the page
# sells, and until 2026-11-13 neither had a price id anywhere in this module:
# the page advertised them, the storage button bounced back to /pricing because
# there was no id to open, and a wallet button called a method Paddle does not
# have. Same failure as the $5/$3 drift, one door further along - an offer the
# page can name and the card form cannot sell.
#
# The amounts are here, beside the ids, and tools/check_catalog.py reads every
# one of them back from Paddle. One table holds id and amount together so they
# cannot drift; the command is what proves the pair against the provider.
DEFAULT_STORAGE_PRICE = {"price_id": "pri_01m719e8r3zqkrshbm6y18jxfp",  # 2.00 monthly
                         "amount_cents": 200, "currency": "usd",
                         "interval": "month"}

# dollars -> sandbox price id for a one-time wallet load. Paddle prices are
# write-once, so an amount change is a new id, never a new amount on an old one.
DEFAULT_WALLET_PRICES = {
    "5": "pri_01m70xwxyrp5629esbr1pzdcb0",
    "10": "pri_01m70xwy09p79a45ec5vs1zqv1",
    "25": "pri_01m70xwy229c08q9nwamw9jgch",
    "50": "pri_01m70xwy3g4ap26merj3kxbxpx",
}
_STORAGE_ENV = "FIRESIDE_PADDLE_STORAGE_PRICE"
_WALLET_ENV = "FIRESIDE_PADDLE_WALLET_PRICES"

# Paddle.js authenticates with its own browser-scoped token, which is NOT the
# API key: the API key must never reach a page.
CLIENT_TOKEN_ENV = "FIRESIDE_PADDLE_CLIENT_TOKEN"
_PLAN_PRICES_ENV = "FIRESIDE_PADDLE_PLAN_PRICES"


def _env(name):
    return (os.environ.get(name) or "").strip()


def known_prices():
    """{price_id: tier}. $FIRESIDE_PADDLE_PRICES replaces the sandbox defaults
    wholesale, so a production catalog never half-inherits sandbox ids.

    The storage block is added here from its own row, not from the tier env: a
    storage subscription arrives as a subscription like any other, and without
    its id in this map `plan_for` would return "" and the event would write no
    room. It is merged in so the two tables cannot disagree about the add-on.
    """
    raw = _env("FIRESIDE_PADDLE_PRICES")
    table = dict(DEFAULT_PRICES)
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            table = {str(k): str(v) for k, v in data.items() if k and v}
    sid = (storage_price() or {}).get("price_id") or ""
    if sid:
        table[sid] = STORAGE_ADDON
    return table


def plan_for(price_id, prices=None):
    """The tier a price id buys, or "" when we do not sell that price. An
    unknown price must never guess a tier: a wrong tier is a wrong allowance."""
    table = known_prices() if prices is None else prices
    return table.get(price_id or "", "")


def storage_price():
    """{price_id, amount_cents, currency, interval} for one storage block.

    $FIRESIDE_PADDLE_STORAGE_PRICE replaces the sandbox row wholesale, the same
    rule as the tier table: a production catalog never half-inherits a sandbox
    id. A malformed override falls back to the default rather than selling a
    block we cannot price.
    """
    raw = _env(_STORAGE_ENV)
    if not raw:
        return dict(DEFAULT_STORAGE_PRICE)
    try:
        data = json.loads(raw)
    except ValueError:
        return dict(DEFAULT_STORAGE_PRICE)
    if not isinstance(data, dict) or not data.get("price_id"):
        return dict(DEFAULT_STORAGE_PRICE)
    out = dict(DEFAULT_STORAGE_PRICE)
    out.update({k: data[k] for k in ("price_id", "amount_cents", "currency",
                                    "interval") if k in data})
    try:
        out["amount_cents"] = int(out["amount_cents"])
    except (TypeError, ValueError):
        return dict(DEFAULT_STORAGE_PRICE)
    return out


def wallet_prices():
    """{dollars as a string: price id} for the one-time wallet loads."""
    raw = _env(_WALLET_ENV)
    if not raw:
        return dict(DEFAULT_WALLET_PRICES)
    try:
        data = json.loads(raw)
    except ValueError:
        return dict(DEFAULT_WALLET_PRICES)
    if not isinstance(data, dict):
        return dict(DEFAULT_WALLET_PRICES)
    out = {str(k): str(v) for k, v in data.items() if k and v}
    return out or dict(DEFAULT_WALLET_PRICES)


def wallet_amount(price_id):
    """Dollars a wallet price id loads, or 0 when we do not sell that price.

    The webhook credits a wallet from this, so an unknown price must credit
    nothing rather than guess an amount: money the card was charged is in
    Paddle's row, not ours, and a wrong number here would hand a stranger the
    difference.
    """
    pid = (price_id or "").strip()
    if not pid:
        return 0
    for usd, known in wallet_prices().items():
        if known == pid:
            try:
                return int(round(float(usd) * 100))
            except (TypeError, ValueError):
                return 0
    return 0


# ---- signature -------------------------------------------------------------

def _fields(header):
    return [(k.lower(), v.strip()) for k, v in _SIG_FIELD.findall(header or "")]


def verify_signature(raw, header, secret, tolerance=None, now=None):
    """None when the delivery is genuine, else a one-line reason.

    `raw` is the exact request body. The header may carry several h1 values
    during a secret rotation, so any match is a match.
    """
    if not secret:
        return "no webhook secret configured"
    body = raw if isinstance(raw, bytes) else (raw or "").encode("utf-8")
    pairs = _fields(header)
    stamps = [v for k, v in pairs if k == "ts"]
    sigs = [v for k, v in pairs if k == "h1"]
    if not stamps or not sigs:
        return "Paddle-Signature has no ts or h1"
    try:
        ts = int(stamps[0])
    except ValueError:
        return "Paddle-Signature has a non-numeric ts"
    tol = DEFAULT_TOLERANCE if tolerance is None else int(tolerance)
    clock = time.time() if now is None else float(now)
    if tol > 0 and abs(clock - ts) > tol:
        return "timestamp outside %ds window (skew %ds)" % (tol, int(ts - clock))
    want = hmac.new(secret.encode("utf-8"),
                    str(ts).encode("ascii") + b":" + body,
                    hashlib.sha256).hexdigest()
    for sig in sigs:
        if hmac.compare_digest(want, sig.lower()):
            return None
    return "signature does not match the body"


# ---- events ----------------------------------------------------------------

def _epoch(iso):
    """ISO-8601 (Paddle sends RFC-3339 with Z) to a unix second. 0 when absent
    or unparseable, which the caller treats as 'no period stated'."""
    if not iso:
        return 0
    text = str(iso).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _custom_user(data):
    cd = (data or {}).get("custom_data") or {}
    if not isinstance(cd, dict):
        return ""
    return str(cd.get(METADATA_KEY) or cd.get("user_id") or "").strip()


def _item_of(items):
    """(price_id, product_id, quantity) of the item that is actually billing.

    Items carry their own status; a superseded or canceled item is not the one
    that decides what the account holds. Quantity comes along because a storage
    block is sold by the block: two blocks are two prices of one subscription,
    and dropping the number would quietly sell one.
    """
    best = ("", "", 0)
    live = ("active", "trialing", "past_due")
    for it in items or []:
        if not isinstance(it, dict):
            continue
        price = it.get("price") if isinstance(it.get("price"), dict) else {}
        pid = price.get("id") or it.get("price_id") or ""
        prod = it.get("product_id") or price.get("product_id") or ""
        try:
            qty = max(1, int(it.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        status = str(it.get("status") or "").lower()
        if status in live:
            return pid, prod, qty
        if not best[0]:
            best = (pid, prod, qty)
    return best


def _price_of(items):
    """(price_id, product_id) for callers that only need what was bought."""
    pid, prod, _qty = _item_of(items)
    return pid, prod


def _subscription(data):
    data = data or {}
    price_id, product_id, quantity = _item_of(data.get("items"))
    period = data.get("current_billing_period") or {}
    change = data.get("scheduled_change") or {}
    return {
        "subscription_id": data.get("id") or "",
        "customer_id": data.get("customer_id") or "",
        "status": str(data.get("status") or "").lower(),
        "price_id": price_id,
        "product_id": product_id,
        "quantity": quantity,
        "plan": plan_for(price_id),
        "scheduled_change_action": str(change.get("action") or ""),
        "scheduled_change_at": _epoch(change.get("effective_at")
                                      or change.get("resume_at")),
        "current_period_end": _epoch(period.get("ends_at")),
        "user_id": _custom_user(data),
    }


def _transaction(data):
    """One payment, with the price it paid for.

    A wallet load is a one-time price and nothing else: there is no subscription
    to confirm and no session for our side to re-read, so the completed
    transaction IS the receipt and this is where the amount has to survive.
    `amount_cents` is Paddle's own total, kept only to log against; the credit
    itself comes from the price id (see wallet_amount), because a total can
    carry tax and a price cannot.
    """
    data = data or {}
    price_id, product_id = _price_of(data.get("items"))
    totals = ((data.get("details") or {}).get("totals") or {})
    try:
        total = int(totals.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    return {"transaction_id": data.get("id") or "",
            "subscription_id": data.get("subscription_id") or "",
            "customer_id": data.get("customer_id") or "",
            "status": str(data.get("status") or "").lower(),
            "price_id": price_id,
            "product_id": product_id,
            "amount_cents": total,
            "currency": str(totals.get("currency_code")
                            or (data.get("currency_code") or "")).lower(),
            "user_id": _custom_user(data)}


def _customer(data):
    data = data or {}
    return {"customer_id": data.get("id") or "",
            "email": data.get("email") or "",
            "user_id": _custom_user(data),
            "status": str(data.get("status") or "").lower()}


def normalize_event(event):
    """One shape for every delivery the core mirrors.

    kind is "customer", "subscription", "transaction" or "ignore". The core
    decides what to store; anything it does not recognize it acknowledges and
    drops, which is what keeps Paddle from retrying a type we do not handle.
    """
    ev = event if isinstance(event, dict) else {}
    etype = str(ev.get("event_type") or "").strip()
    data = ev.get("data") or {}
    out = {"provider": ID,
           "event_id": ev.get("event_id") or "",
           "event_type": etype,
           "notification_id": ev.get("notification_id") or "",
           "occurred_at": ev.get("occurred_at") or "",
           "kind": "ignore",
           "customer": None,
           "subscription": None,
           "transaction": None}
    if etype.startswith("customer."):
        out["kind"] = "customer"
        out["customer"] = _customer(data)
    elif etype.startswith("subscription."):
        sub = _subscription(data)
        out["kind"] = "subscription"
        out["subscription"] = sub
        # The subscription event names its customer but not the email; this stub
        # links it, and the customer.* event fills in the address later.
        out["customer"] = {"customer_id": sub["customer_id"], "email": "",
                           "user_id": sub["user_id"], "status": ""}
    elif etype.startswith("transaction."):
        out["kind"] = "transaction"
        out["customer"] = _customer(data)
        out["transaction"] = _transaction(data)
    return out


# ---- HTTP ------------------------------------------------------------------

def _call(method, url, key, payload=None, timeout=15):
    """(json, error). One plain HTTPS call; the container needs no SDK."""
    if not key:
        return None, "no Paddle API key configured"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return (json.loads(raw) if raw else {}), ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        return None, "HTTP %s %s" % (e.code, detail)
    except Exception as e:                                  # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


class Paddle:
    """The provider object host.payments registers."""

    id = ID

    def __init__(self, api_key="", webhook_secret="", env="sandbox",
                 tolerance=None, base_url=None, client_token=""):
        self.api_key = api_key or ""
        self.webhook_secret = webhook_secret or ""
        self.client_token = client_token or ""
        self.env = (env or "sandbox").strip().lower()
        self.tolerance = DEFAULT_TOLERANCE if tolerance is None else int(tolerance)
        self.base_url = base_url or (LIVE_API if self.env == "production"
                                     else SANDBOX_API)
        self._plans_cache = None

    # -- catalog -------------------------------------------------------------
    def plans(self):
        """What each rung and the storage block actually costs, from Paddle.

        host.plans.catalog() asks this so the page prints the provider's number
        instead of falling back to our own list. Until 2026-11-13 there was no
        such method, every tier's amount came back None, and the page printed
        LIST_PRICE_CENTS - a second price table that happened to agree with the
        provider's that day. That is exactly the shape of the $5/$3 drift, with
        the two tables reversed.

        One call, cached briefly: a page render must not wait on Paddle, and a
        failed read must return [] (the page then says the price is not set)
        rather than invent one.
        """
        cached = self._plans_cache
        if cached and (time.time() - cached[0]) < PRICE_CACHE_SECONDS:
            return [Plan(**dict(p)) for p in cached[1]]
        data, err = _call("GET", "%s/prices?per_page=50" % self.base_url,
                          self.api_key, timeout=6)
        if err or not isinstance(data, dict):
            return []
        by_id = {}
        for row in data.get("data") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            try:
                cents = int(((row.get("unit_price") or {}).get("amount")) or 0)
            except (TypeError, ValueError):
                cents = 0
            interval = ((row.get("billing_cycle") or {}).get("interval") or "")
            by_id[row["id"]] = {"amount_cents": cents,
                                "currency": str((row.get("unit_price") or {})
                                                .get("currency_code")
                                                or "usd").lower(),
                                "interval": interval or None,
                                "status": str(row.get("status") or "").lower()}
        out = []
        for tier, cycles in known_plan_prices().items():
            # month first: host.plans asks for one number per rung, and the page
            # prints the monthly figure with the annual as a second line.
            for cycle in ("month", "year"):
                pid = (cycles or {}).get(cycle)
                row = by_id.get(pid or "")
                if not row or row["status"] != "active":
                    continue
                out.append({"id": tier, "label": tier,
                            "amount_cents": row["amount_cents"],
                            "currency": row["currency"],
                            "interval": row["interval"] or cycle,
                            "price": pid})
        st = storage_price()
        row = by_id.get(st.get("price_id") or "")
        if row and row["status"] == "active":
            out.append({"id": STORAGE_ADDON, "label": "Storage block",
                        "amount_cents": row["amount_cents"],
                        "currency": row["currency"],
                        "interval": row["interval"] or "month",
                        "price": st["price_id"]})
        self._plans_cache = (time.time(), [dict(p) for p in out])
        return [Plan(**dict(p)) for p in out]

    def wallet_amount(self, price_id):
        """Dollars a wallet price id loads, 0 when we do not sell it."""
        return wallet_amount(price_id)

    # -- webhooks ------------------------------------------------------------
    def verify_webhook(self, raw, signature):
        """(event, "") on a genuine delivery, (None, reason) otherwise.

        Nothing is read out of the body before the signature checks out.
        """
        err = verify_signature(raw, signature, self.webhook_secret,
                               self.tolerance)
        if err:
            return None, err
        try:
            event = json.loads(raw.decode("utf-8")
                               if isinstance(raw, bytes) else raw)
        except (ValueError, UnicodeDecodeError):
            return None, "verified body is not JSON"
        if not isinstance(event, dict):
            return None, "verified body is not an event object"
        return event, ""

    def normalize_event(self, event):
        return normalize_event(event)

    # -- checkout ------------------------------------------------------------
    def checkout(self, plan_id="", meta=None):
        """Where to send a buyer. Paddle's overlay lives on our pricing page, so
        that page is the answer; no transaction is created here, which is what
        keeps the charged amount the one Paddle holds."""
        return Checkout(ok=True, url="/pricing", reference="",
                        detail="Paddle checkout opens on the pricing page")

    def public_config(self):
        """What the pricing page may know (see the module-level function).

        A method on the provider, not a bare module call off the route, so the
        host asks every provider the same question. An instance whose token was
        handed to it at construction answers with that token, not the env's.
        """
        conf = public_config()
        if self.client_token:
            conf["ok"] = True
            conf["client_token"] = self.client_token
            conf["detail"] = ""
        return conf

    # -- portal and cancel ---------------------------------------------------
    def portal(self, customer_id, return_url=""):
        """{"ok","url","detail"} for the Paddle-hosted account page.

        `customer_id` is resolved by the caller from the session; Paddle's
        portal has no return-url parameter, so `return_url` is accepted and
        ignored rather than silently dropped by the caller.
        """
        if not customer_id:
            return {"ok": False, "url": "", "detail": "no customer id"}
        url = "%s/customers/%s/portal-sessions" % (self.base_url, customer_id)
        data, err = _call("POST", url, self.api_key, {})
        if err:
            return {"ok": False, "url": "", "detail": err}
        try:
            overview = data["data"]["urls"]["general"]["overview"]
        except (KeyError, TypeError):
            return {"ok": False, "url": "", "detail": "no overview url in session"}
        return {"ok": True, "url": overview, "detail": ""}

    def cancel(self, subscription_id):
        """Stop the next renewal at the end of the paid period.

        Paddle keeps the subscription active and records a scheduled_change
        until then, so access runs to the period end by construction; the
        subscription.updated event that follows is what mirrors it.
        """
        sid = (subscription_id or "").strip()
        if not sid:
            return {"ok": False, "detail": "no subscription id"}
        url = "%s/subscriptions/%s/cancel" % (self.base_url, sid)
        data, err = _call("POST", url, self.api_key,
                          {"effective_from": "next_billing_period"})
        if err:
            return {"ok": False, "detail": err}
        change = ((data or {}).get("data") or {}).get("scheduled_change") or {}
        # The route reports these two fields to the screen and to its caller,
        # and used to read them off a dict that never carried them: a cancel
        # that worked perfectly answered cancel_at_period_end=false and
        # current_period_end=0 (found 2026-11-13 by cancelling a real sandbox
        # subscription and reading the JSON). The truth is in the scheduled
        # change Paddle just echoed.
        end = _epoch((change or {}).get("effective_at"))
        return {"ok": True, "detail": change.get("action", ""),
                "cancel_at_period_end": bool(change.get("action") == "cancel"),
                "current_period_end": end}

    # -- plan change and resume ----------------------------------------------
    def change_plan(self, subscription_id, plan, cycle=""):
        """Move an existing subscription onto another of our tiers.

        Paddle takes the COMPLETE item list, so the body names the one price the
        subscription should have, and proration is immediate because that is the
        mode Chris picked (2026-11-09). The cycle stays what the subscriber
        bought: a monthly subscriber moving up stays monthly. Checked against
        Paddle's own update-subscription doc (11-10): items 1-100, each with
        price_id and quantity, and proration_billing_mode is required whenever
        items change.
        """
        sid = (subscription_id or "").strip()
        if not sid:
            return {"ok": False, "detail": "no subscription id"}
        prices = known_plan_prices()
        cycles = prices.get((plan or "").strip()) or {}
        if not cycles:
            return {"ok": False, "detail": "no price for that plan"}
        want = (cycle or "").strip()
        if not want:
            # read the cycle off the subscription rather than assume monthly
            data, err = _call("GET", "%s/subscriptions/%s" % (self.base_url, sid),
                              self.api_key)
            if err:
                return {"ok": False, "detail": err}
            items = ((data or {}).get("data") or {}).get("items") or []
            cur = ""
            if items and isinstance(items[0], dict):
                cur = ((items[0].get("price") or {}).get("id")
                       or items[0].get("price_id") or "")
            want = cycle_of_price(cur, prices)
        price_id = cycles.get(want) or cycles.get("month") or sorted(cycles.values())[0]
        data, err = _call("PATCH", "%s/subscriptions/%s" % (self.base_url, sid),
                          self.api_key,
                          {"items": [{"price_id": price_id, "quantity": 1}],
                           "proration_billing_mode": "prorated_immediately"})
        if err:
            return {"ok": False, "detail": err}
        return {"ok": True, "price_id": price_id, "cycle": want,
                "detail": ""}

    def resume(self, subscription_id):
        """Undo a scheduled cancel.

        Paddle's update-subscription doc, read 2026-11-10: `scheduled_change`
        may only be set to null when updating, and that removes the scheduled
        change. So the next charge comes back.
        """
        sid = (subscription_id or "").strip()
        if not sid:
            return {"ok": False, "detail": "no subscription id"}
        data, err = _call("PATCH", "%s/subscriptions/%s" % (self.base_url, sid),
                          self.api_key, {"scheduled_change": None})
        if err:
            return {"ok": False, "detail": err}
        return {"ok": True, "detail": ""}


def cycle_of_price(price_id, prices=None):
    """'month' or 'year' for one of our price ids, "" when we do not sell it.

    A plan change must stay on the cycle the subscriber already bought. The
    cycle is not stored on our row (the provider owns it), so it is read back
    from the price id the provider has on the subscription.
    """
    table = known_plan_prices() if prices is None else prices
    for cycles in (table or {}).values():
        for name, pid in (cycles or {}).items():
            if pid == price_id:
                return name
    return ""


def known_plan_prices():
    """tier -> {"month": id, "year": id} for the page. $FIRESIDE_PADDLE_PLAN_PRICES
    replaces the sandbox table wholesale, same rule as $FIRESIDE_PADDLE_PRICES."""
    default = {t: dict(c) for t, c in DEFAULT_PLAN_PRICES.items()}
    raw = _env(_PLAN_PRICES_ENV)
    if not raw:
        return default
    try:
        data = json.loads(raw)
    except ValueError:
        return default
    if not isinstance(data, dict):
        return default
    out = {}
    for tier, cycles in data.items():
        if isinstance(cycles, dict):
            clean = {str(k): str(v) for k, v in cycles.items() if k and v}
            if clean:
                out[str(tier)] = clean
    return out or default


def public_config():
    """What the pricing page needs to open the overlay, and nothing else.

    A client token is Paddle's own browser key (read the catalog, open a
    checkout) and a price id is already printed on the page, so this route can
    be public. The API key and the webhook secret never leave the server: a
    leaked API key can move money, a leaked client token cannot.
    """
    token = _env(CLIENT_TOKEN_ENV)
    env = _env("FIRESIDE_PADDLE_ENV").lower()
    if not env:
        env = "production" if "live" in token else "sandbox"
    st = storage_price()
    out = {"ok": bool(token), "provider": ID, "env": env,
           "prices": known_plan_prices(),
           # The other two things the page sells need an id in the browser for
           # the same reason a plan does: the overlay opens on the price id, and
           # without one the button has nothing to open. Both are public facts
           # (a price id is already printed; the amount is on the page).
           "storage_price": {"price_id": st.get("price_id") or "",
                             "amount_cents": st.get("amount_cents"),
                             "interval": st.get("interval") or "month"},
           "wallet": wallet_prices(),
           "currency": "USD",
           "detail": "" if token else "no client token configured"}
    if token:
        out["client_token"] = token
    return out


def from_env():
    """Register only when a deployment actually configured Paddle.

    One half configured is still registered, on purpose: a webhook with no
    secret then says "no webhook secret configured" out loud instead of a
    missing provider silently swallowing every delivery.
    """
    key = _env("FIRESIDE_PADDLE_API_KEY")
    secret = _env("FIRESIDE_PADDLE_WEBHOOK_SECRET")
    if not key and not secret:
        return None
    env = _env("FIRESIDE_PADDLE_ENV").lower()
    if not env:
        env = "production" if "live" in key else "sandbox"
    tol = _env("FIRESIDE_PADDLE_SIGNATURE_TOLERANCE")
    try:
        tolerance = DEFAULT_TOLERANCE if not tol else int(tol)
    except ValueError:
        tolerance = DEFAULT_TOLERANCE
    return Paddle(key, secret, env, tolerance,
                  client_token=_env(CLIENT_TOKEN_ENV))
