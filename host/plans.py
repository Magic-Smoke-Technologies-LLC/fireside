"""The plan catalog: what a user can be on, and what each includes.

One place turns quota's policy constants and the payment provider's price list
into the shape the screens read. It names two tiers and one add-on:

  free      the sign-up default; an allowance, no price
  platform  the paid tier that funds the server; its price is the provider's
  storage   an add-on sold by the block; its price is the provider's too

Prices are never invented here. A tier or a block with no provider price reports
`amount_cents: None` and the screen says the price is not set yet, which is the
honest state until a payment provider is registered.
"""

from host import quota

STORAGE_ADDON = quota.STORAGE_ADDON


def _price_of(provider, plan_id):
    """(amount_cents, currency, interval) a provider charges for `plan_id`, or
    (None, 'usd', interval-guess) when there is no provider or no such plan."""
    fn = getattr(provider, "plans", None)
    if not callable(fn):
        return None, "usd", None
    try:
        plans = fn() or []
    except Exception:
        return None, "usd", None
    for p in plans:
        d = p.dict() if hasattr(p, "dict") else dict(p)
        if d.get("id") == plan_id:
            return d.get("amount_cents"), d.get("currency", "usd"), d.get("interval")
    return None, "usd", None


# The published list price, monthly and annual, in cents. This is our own price
# (docs/PRICING.md, the price view in ui/index.html), not a provider's. It exists so a screen can
# print the real numbers while payments are still switched off, labelling them
# "not for sale yet" instead of printing "price not set", which told a reader
# nothing about what the tier costs. When a provider IS registered, its price
# wins; a disagreement is the provider's number that charges the card.
LIST_PRICE_CENTS = {quota.PLATFORM_PLAN: (300, 3600),
                    quota.LISTENER_PLAN: (800, 9600),
                    quota.STUDIO_PLAN: (2000, 24000)}

# What each rung is called on the screen, and one line on who it is for. Prices
# stay with the provider (see _price_of): this module only knows shape.
TIER_META = {
    quota.FREE_PLAN: ("Free", "A few books, no card, no expiry."),
    quota.PLATFORM_PLAN: ("Reader", "Enough to listen to a novel a month."),
    quota.LISTENER_PLAN: ("Listener", "Series, long fics, and a shelf that grows."),
    quota.STUDIO_PLAN: ("Studio", "The same characters bought outright, plus the platform."),
}


def catalog(provider=None):
    """The offering, ready to render.

    `tiers` are exclusive choices (you are on exactly one); `storage` is an
    add-on you buy N blocks of, on top of any tier.
    """
    blk_cents, blk_cur, blk_int = _price_of(provider, STORAGE_ADDON)
    configured = provider is not None
    tiers = []
    for pid in quota.TIERS:
        label, description = TIER_META.get(pid, (pid, ""))
        if pid == quota.FREE_PLAN:
            cents, cur, interval = 0, "usd", None
        else:
            cents, cur, interval = _price_of(provider, pid)
        list_m, list_y = LIST_PRICE_CENTS.get(pid, (None, None))
        tiers.append({"id": pid, "label": label,
                      "amount_cents": cents, "currency": cur,
                      "list_amount_cents": list_m, "list_annual_cents": list_y,
                      # A dollar amount, because the wallet is the unit now;
                      # a card that printed characters would be quoting a
                      # number the reader cannot spend.
                      "included_usd": quota.PLAN_GRANT.get(pid, 0.0),
                      "interval": interval or (None if pid == quota.FREE_PLAN else "month"),
                      "included_bytes": quota.PLAN_ALLOWANCE.get(pid, 0),
                      "description": description})
    return {
        "payments_configured": configured,
        "tiers": tiers,
        "storage": {
            "addon": STORAGE_ADDON,
            "block_bytes": quota.STORAGE_BLOCK_BYTES,
            "amount_cents": blk_cents, "currency": blk_cur,
            "interval": blk_int or "month",
            "description": "Add storage in blocks, monthly. Buy as many as you need.",
        },
    }
