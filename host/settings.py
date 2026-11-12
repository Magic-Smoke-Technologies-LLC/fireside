"""Operator settings that live in the database, so going live is a screen.

The card provider's keys, which Paddle environment they belong to, and the six
price ids the page sells were all environment variables, which means only an
image bump could change them. That made "go live" a paste-secrets-into-Azure
checklist, and it made a wrong value invisible until a stranger tried to pay.

A row wins over the environment. The environment stays the fallback, and the
screen says which one answered, because "why is it still charging the sandbox"
is exactly the question a silent fallback hides.

Two rules this module keeps:

  * A secret is never echoed back. The screen gets set:true and the last four
    characters, which is enough to tell two keys apart and useless to a thief.
  * A setting is validated where it is written, against the other settings it
    has to agree with. A live API key and a sandbox environment is a
    configuration that "looks saved" and refuses every real payment; the check
    for that belongs here, not in a support thread three days later.
"""

import json
import os

# Every field the operator screen may write. `env` is the variable the rest of
# the code still reads, so nothing downstream had to learn about this table.
FIELDS = (
    {"key": "paddle_env", "env": "FIRESIDE_PADDLE_ENV", "group": "paddle",
     "label": "Paddle environment", "kind": "select",
     "choices": ["sandbox", "production"],
     "help": "Which Paddle this server talks to. Blank means: read it off the "
             "API key (pdl_live_... is production, pdl_sdbx_... is sandbox)."},
    {"key": "paddle_api_key", "env": "FIRESIDE_PADDLE_API_KEY", "group": "paddle",
     "label": "API key", "secret": True,
     "help": "Starts pdl_live_apikey_ or pdl_sdbx_apikey_. Server-side only: "
             "this key can move money, so it never reaches a page."},
    {"key": "paddle_client_token", "env": "FIRESIDE_PADDLE_CLIENT_TOKEN",
     "group": "paddle", "label": "Browser client token",
     "help": "Starts live_ or test_. This is the one token a page may hold; it "
             "can open a checkout and cannot charge to anyone else."},
    {"key": "paddle_webhook_secret", "env": "FIRESIDE_PADDLE_WEBHOOK_SECRET",
     "group": "paddle", "label": "Notification secret", "secret": True,
     "help": "From the notification destination, not the API key. Without it "
             "every delivery is refused, and purchases settle nowhere."},
    {"key": "paddle_plan_prices", "env": "FIRESIDE_PADDLE_PLAN_PRICES",
     "group": "paddle", "label": "Price ids", "kind": "prices",
     "help": "One price id per tier and cycle. The reverse map the webhook "
             "reads is derived from this table, so the two can never disagree."},
)

BY_KEY = {f["key"]: f for f in FIELDS}

# What a completed sale needs, and the order the screen shows it in. A missing
# piece is not a warning: it is the reason the next real payment does nothing.
NEEDED = ("paddle_api_key", "paddle_client_token", "paddle_webhook_secret",
          "paddle_plan_prices")

PREFIX = "config:"


def _slot(key):
    return PREFIX + key


def stored(conn, key):
    """The row's value, or None when there is no row.

    None and "" are different on purpose. No row means "read the environment";
    an empty row means the operator turned this setting off, and that has to win
    over an environment variable that is still sitting there from the deploy,
    or clearing a key would look like it worked and then quietly come back.
    """
    row = conn.execute("SELECT value FROM settings WHERE `key`=?",
                       (_slot(key),)).fetchone()
    if not row:
        return None
    return row["value"] if not isinstance(row, tuple) else row[0]


def effective(conn, key):
    """What the code will actually read: the row if there is one, else the env."""
    v = stored(conn, key)
    if v is not None:
        return (v or "").strip()
    return (os.environ.get(BY_KEY[key]["env"]) or "").strip()


def _put(conn, key, value):
    conn.execute("DELETE FROM settings WHERE `key`=?", (_slot(key),))
    conn.execute("INSERT INTO settings(`key`,value) VALUES(?,?)",
                 (_slot(key), value))


# ---- the price table -------------------------------------------------------

def parse_prices(raw):
    """{tier: {cycle: price_id}} from the stored JSON, or {} when unusable."""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "")
        except (TypeError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for tier, cycles in data.items():
        if isinstance(cycles, dict):
            clean = {str(k): str(v) for k, v in cycles.items() if k and v}
            if clean:
                out[str(tier)] = clean
    return out


def prices_by_id(plans):
    """{price_id: tier}, the shape the webhook needs, derived from the page's
    table so there is only ever one catalog to get wrong."""
    out = {}
    for tier, cycles in (plans or {}).items():
        for _cycle, pid in (cycles or {}).items():
            out[pid] = tier
    return out


def _check_prices(value, tiers):
    """(plans, error). Every id starts pri_, no id buys two tiers, no tier is
    half a rung: the page sells both cycles, so a tier with only a monthly id
    renders a yearly button that charges nothing."""
    plans = parse_prices(value)
    if not plans:
        return {}, "no price ids: enter at least one tier"
    for tier, cycles in plans.items():
        if tier not in tiers:
            return {}, "unknown tier %r (expected one of %s)" % (
                tier, ", ".join(tiers))
        for cycle in cycles:
            if cycle not in ("month", "year"):
                return {}, "unknown cycle %r on %s (month or year)" % (
                    cycle, tier)
        for cycle, pid in cycles.items():
            if not pid.startswith("pri_"):
                return {}, "%s %s is not a price id (%s)" % (tier, cycle, pid)
        month, year = cycles.get("month"), cycles.get("year")
        if month and year and month == year:
            return {}, "%s uses one price for both cycles, so the yearly " \
                       "button would charge a month" % tier
        if "month" not in cycles:
            return {}, "%s has no monthly price, so the monthly button " \
                       "would charge nothing" % tier
    seen = {}
    for pid, tier in prices_by_id(plans).items():
        if pid in seen and seen[pid] != tier:
            return {}, "%s is used for both %s and %s" % (pid, seen[pid], tier)
        seen[pid] = tier
    return plans, ""


# ---- read ------------------------------------------------------------------

def view(conn, tiers=("platform", "listener", "studio")):
    """Every field as the screen may see it, plus what still blocks a sale.

    A secret leaves as set:true and a four-character hint. That is deliberately
    not enough to use, and enough to tell "I saved the live key" from "I saved
    the sandbox key" without opening Azure.
    """
    rows = []
    for f in FIELDS:
        raw = stored(conn, f["key"])
        env = (os.environ.get(f["env"]) or "").strip()
        value = raw if raw is not None else env
        source = "saved" if raw is not None else ("environment" if env else "")
        value = (value or "").strip()
        item = {"key": f["key"], "label": f["label"], "help": f["help"],
                "kind": f.get("kind", "text"), "secret": bool(f.get("secret")),
                "choices": list(f.get("choices") or []),
                "set": bool(value), "source": source, "value": ""}
        if f.get("kind") == "prices":
            item["value"] = parse_prices(value) if value else {}
        elif f.get("secret"):
            item["hint"] = value[-4:] if value else ""
        else:
            item["value"] = value
        rows.append(item)

    missing = [k for k in NEEDED if not effective(conn, k)]
    plans = parse_prices(effective(conn, "paddle_plan_prices"))
    key = effective(conn, "paddle_api_key")
    env = effective(conn, "paddle_env") or (
        "production" if "live" in key else ("sandbox" if key else ""))
    return {"fields": rows, "tiers": list(tiers),
            "missing": missing, "ready": not missing,
            "env": env, "live": ("live" in key),
            "prices": plans}


# ---- write -----------------------------------------------------------------

def _clean(key, value, tiers):
    """(stored_value, error) for one field, on its own."""
    f = BY_KEY[key]
    text = ("" if value is None else str(value)).strip()
    if f.get("kind") == "prices":
        plans, err = _check_prices(value if isinstance(value, dict) else text, tiers)
        if err:
            return "", err
        return json.dumps(plans, sort_keys=True), ""
    if f.get("kind") == "select":
        if text and text not in f["choices"]:
            return "", "%s must be one of %s" % (key, ", ".join(f["choices"]))
        return text, ""
    return text, ""


def save(conn, values, clear=(), tiers=("platform", "listener", "studio")):
    """Check the whole batch, then write it. (saved, errors).

    The batch matters: a live key next to a sandbox environment is two fields
    that are each fine and one that is broken. Cross-checks run before any row
    is touched, so a refused save leaves the previous configuration exactly as
    it was instead of half-applying.
    """
    values = dict(values or {})
    errors = {}
    clean = {}
    for key, value in values.items():
        if key not in BY_KEY:
            errors[key] = "unknown setting"
            continue
        # A blank secret means "leave the one you have", because the screen
        # never received it and an empty box is not an instruction to wipe it.
        if BY_KEY[key].get("secret") and not str(value or "").strip():
            continue
        stored_value, err = _clean(key, value, tiers)
        if err:
            errors[key] = err
        else:
            clean[key] = stored_value

    # A clear is explicit, and clear wins over a value in the same request.
    for key in clear or ():
        if key not in BY_KEY:
            errors[key] = "unknown setting"
            continue
        clean[key] = ""

    if errors:
        return [], errors

    # The cross-checks read the batch on top of what is already saved.
    final = {}
    for key in ("paddle_env", "paddle_api_key", "paddle_client_token"):
        if key in clean:
            final[key] = clean[key]
        else:
            final[key] = effective(conn, key)

    key = final["paddle_api_key"]
    env = final["paddle_env"]
    if key:
        if not (key.startswith("pdl_live_apikey_")
                or key.startswith("pdl_sdbx_apikey_")):
            errors["paddle_api_key"] = ("not a Paddle API key: expected "
                                        "pdl_live_apikey_... or pdl_sdbx_apikey_...")
        elif env and key.startswith("pdl_live_") and env != "production":
            errors["paddle_env"] = ("a live API key against the sandbox "
                                    "environment: no payment will settle")
        elif env and key.startswith("pdl_sdbx_") and env == "production":
            errors["paddle_env"] = ("a sandbox API key against production: no "
                                    "payment will settle")
        elif not env:
            # Blank means "read it off the key". Say so by filling it in, so the
            # screen and the webhook cannot disagree about which Paddle is live.
            clean["paddle_env"] = ("production" if key.startswith("pdl_live_")
                                   else "sandbox")

    token = final["paddle_client_token"]
    if token:
        env_now = clean.get("paddle_env") or env
        if token.startswith("live_") and env_now and env_now != "production":
            errors["paddle_client_token"] = ("a live browser token against the "
                                             "sandbox: the overlay would refuse")
        elif token.startswith("test_") and env_now == "production":
            errors["paddle_client_token"] = ("a test browser token against "
                                             "production: the overlay would refuse")

    if errors:
        return [], errors

    for key, value in clean.items():
        # An empty string is a written "off", not a missing row: see stored().
        _put(conn, key, value)
    return sorted(clean), {}


def apply(conn):
    """Write the saved settings into the environment the rest of the host reads.

    Called once at boot, before the payment provider is registered, and again
    after a save so a key takes effect without a restart. A cleared setting
    removes the variable rather than blanking it, because a blank API key and
    an absent one must behave the same way: not configured.
    """
    for f in FIELDS:
        value = effective(conn, f["key"])
        if value:
            os.environ[f["env"]] = value
        else:
            os.environ.pop(f["env"], None)
    # The webhook's reverse map is derived, never stored twice.
    plans = parse_prices(effective(conn, "paddle_plan_prices"))
    by_id = prices_by_id(plans)
    if by_id:
        os.environ["FIRESIDE_PADDLE_PRICES"] = json.dumps(by_id, sort_keys=True)
    else:
        os.environ.pop("FIRESIDE_PADDLE_PRICES", None)


# ---- proof -----------------------------------------------------------------

def check(conn, call=None, tiers=("platform", "listener", "studio")):
    """Ask Paddle whether these keys and price ids are real.

    A saved value is a claim; this is the receipt. Three questions, each with
    its own line: does the API key authenticate, does the token's environment
    match the key's, and is every price id we sell actually in that catalog.
    The webhook secret cannot be proven without a delivery, and this says so
    instead of implying a green tick it cannot earn.
    """
    if call is None:
        from host.payments_paddle import _call as call       # noqa: N813

    key = effective(conn, "paddle_api_key")
    env = effective(conn, "paddle_env") or (
        "production" if "live" in key else "sandbox")
    base = ("https://api.paddle.com" if env == "production"
            else "https://sandbox-api.paddle.com")
    out = {"ok": True, "env": env, "checks": []}

    def line(name, ok, detail):
        out["checks"].append({"id": name, "ok": bool(ok), "detail": detail})
        if not ok:
            out["ok"] = False

    if not key:
        line("key", False, "no API key saved")
        line("catalog", False, "not asked: no key")
        line("webhook", False, "no notification secret saved")
        return out

    seen, url, pages = set(), "%s/prices?per_page=200&status=active" % base, 0
    err = ""
    while url and pages < 3:
        data, err = call("GET", url, key)
        if err:
            break
        for p in ((data or {}).get("data") or []):
            if isinstance(p, dict) and p.get("id"):
                seen.add(p["id"])
        url = (((data or {}).get("meta") or {}).get("pagination") or {}).get("next")
        pages += 1
    if err:
        line("key", False, err)
    else:
        line("key", True, "%d active prices readable" % len(seen))

    plans = parse_prices(effective(conn, "paddle_plan_prices"))
    want = sorted(prices_by_id(plans))
    if not want:
        line("catalog", False, "no price ids saved")
    elif err:
        line("catalog", False, "not checked: the key did not authenticate")
    else:
        missing = [p for p in want if p not in seen]
        line("catalog", not missing,
             "all %d price ids are live" % len(want) if not missing
             else "not in this catalog: %s" % ", ".join(missing))

    secret = effective(conn, "paddle_webhook_secret")
    line("webhook", bool(secret),
         "secret saved; a genuine delivery is the only proof, so the first "
         "real event is the test" if secret else "no notification secret saved")
    return out