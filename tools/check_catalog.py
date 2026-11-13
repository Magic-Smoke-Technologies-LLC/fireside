#!/usr/bin/env python3
"""Read the price ids back from the payment provider and compare them to the
numbers the page prints.

Receipt for a real miss (2026-11-13): the pricing page said Reader $5.00 a
month, the subscribe button opened Paddle's overlay, and the overlay asked the
card for $3.00. Two tables had drifted. host/plans.py LIST_PRICE_CENTS is what
the page prints; host/payments_paddle.py's price table is what actually gets
charged. Nothing in the test suite can see that, because both tables are
consistent with themselves and the disagreement only exists inside Paddle's
catalog.

    FIRESIDE_PADDLE_API_KEY=pdl_sdbx_... python3 tools/check_catalog.py
    FIRESIDE_PADDLE_API_KEY=pdl_live_... python3 tools/check_catalog.py --env production

Exit 0 only when every id we sell resolves to the list price for its tier and
cycle, and every currency override names a currency the price carries. Anything
else prints the id, the tier, what we say it costs, what Paddle says it costs,
and exits 1.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host import payments_paddle as pp  # noqa: E402
from host import plans  # noqa: E402
from host import quota  # noqa: E402

API = {"sandbox": "https://sandbox-api.paddle.com",
       "production": "https://api.paddle.com"}


def price_of(api_key, env, price_id):
    url = "%s/prices/%s" % (API[env], price_id)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + api_key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read()).get("data") or {}
    except urllib.error.HTTPError as e:
        return {"_error": "%d %s" % (e.code, e.read()[:200].decode("utf-8", "replace"))}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env", default="sandbox", choices=sorted(API))
    p.add_argument("--api-key", default=os.environ.get("FIRESIDE_PADDLE_API_KEY", ""))
    args = p.parse_args(argv)
    if not args.api_key:
        print("no api key: set FIRESIDE_PADDLE_API_KEY")
        return 2

    bad = 0
    table = pp.known_plan_prices()
    for tier in (quota.PLATFORM_PLAN, quota.LISTENER_PLAN, quota.STUDIO_PLAN):
        want_month, want_year = plans.LIST_PRICE_CENTS[tier]
        for cycle, want in (("month", want_month), ("year", want_year)):
            pid = (table.get(tier) or {}).get(cycle) or ""
            if not pid:
                print("MISSING  %-9s %-5s no price id wired" % (tier, cycle))
                bad += 1
                continue
            d = price_of(args.api_key, args.env, pid)
            if d.get("_error"):
                print("ERROR    %-9s %-5s %s %s" % (tier, cycle, pid, d["_error"]))
                bad += 1
                continue
            got = int((d.get("unit_price") or {}).get("amount") or 0)
            status = d.get("status") or ""
            label = "%s %s" % ((d.get("unit_price") or {}).get("currency_code") or "", got)
            if got != want:
                print("MISMATCH %-9s %-5s %s page says %d, Paddle charges %s"
                      % (tier, cycle, pid, want, label))
                bad += 1
            elif status != "active":
                print("NOT LIVE %-9s %-5s %s status=%s" % (tier, cycle, pid, status))
                bad += 1
            else:
                print("ok       %-9s %-5s %s %s" % (tier, cycle, pid, label))

    print("---")
    print("catalog agrees with the page" if not bad else "%d disagreement(s)" % bad)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())