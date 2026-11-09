# Paddle: checkout in the page, fulfillment on the server

Paddle Billing, sandbox first. Nothing here is live until the operator sets the
two secrets below and a notification destination exists.

## The shape

```
  /pricing (Paddle.js overlay)        Paddle                       us
  ----------------------------        ------                       --
  buyer clicks a rung  ─────────────▶ checkout collects card
                                      money settles
                                      signed webhook ────────────▶ POST /webhook/paddle
                                                                     verify Paddle-Signature
                                                                     normalize the event
                                                                     mirror customer + subscription
                                                                     project the tier into subscriptions
                                      ◀──── 200 in under 5s ──────
  account screen ──▶ POST /billing/portal ──▶ portal-sessions ──▶ Paddle-hosted page
```

We never create a transaction and never compute a price. The amount charged is
the one Paddle holds for the price id. That is also why a new buyer has no row
on our side when the money moves: the first webhook has to find the account by
itself.

## Files

| file | what it is |
|---|---|
| `host/payments_paddle.py` | the provider: signature check, event normalization, portal, cancel, price→tier map |
| `host/billing.py` | the mirror tables and the projection into `subscriptions` (`apply_event`, `mirror_subscription`, `project`) |
| `host/db.py` | `paddle_customers`, `paddle_subscriptions`, `paddle_events` |
| `host/app.py` | `POST /webhook/paddle` (public, signature is the auth) and the `/billing` access flags |
| `tests/test_paddle.py` | signature, replay, ordering, access, portal |

## Signature

`Paddle-Signature: ts=<unix>;h1=<hex>`, where `h1 = HMAC-SHA256(secret, "<ts>:<raw body>")`.

The route hands the **raw bytes** to the verifier before anything parses them. A
body that has been parsed and re-serialized does not match, which is the single
most common way this breaks. The timestamp window is 5 seconds, matching the
Paddle SDKs; `FIRESIDE_PADDLE_SIGNATURE_TOLERANCE` widens or disables it when a
clock is wrong, and a mismatch returns 4xx so Paddle retries instead of treating
the delivery as done.

## Idempotency and ordering

Deliveries are at-least-once and can arrive out of order.

* Every applied `event_id` is remembered in `paddle_events`. A replay is
  acknowledged and dropped.
* Each subscription row carries the `occurred_at` of the delivery that wrote it.
  An older delivery is acknowledged and dropped, so a slow retry cannot rewind a
  cancel.
* Handlers upsert on Paddle's own ids, never blind-insert.

## Who gets access

`billing.active()` is the single decision, and it needs both:

1. status `active` or `trialing`, and
2. a paid period that has not lapsed past the 3-day grace.

So `past_due`, `paused`, `canceled` and `expired` do not grant access. A
`scheduled_change` (a pending cancel or pause) is **not** a status change: Paddle
leaves the subscription active until the change takes effect, so access runs to
the end of the paid period by construction. Card failures during dunning will cut
access off; that is the conservative reading, and the mirror keeps the real
status so the rule can be loosened later without losing history.

`billing.grants_access(status)` mirrors that status test so the two helpers cannot
drift apart.

## Which account bought it

In order: `custom_data.fireside_user` on the Paddle subscription → the mirrored
customer's `user_id` → the customer email matched against `local_accounts` or
`identities`. Nothing is guessed beyond that. If none of the three hit, the row is
mirrored with no user and no tier is granted, which is visible in
`paddle_subscriptions.user_id = ''` rather than silently misgranted.

The page must therefore send `custom_data: {"fireside_user": "<uid>"}` at
checkout. Without it, an email mismatch means a paying customer gets nothing.

## Environment

```
FIRESIDE_PAYMENT_PROVIDER=host.payments_paddle
FIRESIDE_PADDLE_API_KEY=pdl_sdbx_apikey_...        sandbox key
FIRESIDE_PADDLE_WEBHOOK_SECRET=pdl_ntfset_...      notification destination SECRET KEY (not the API key)
FIRESIDE_PADDLE_ENV=sandbox                        sandbox | production
FIRESIDE_PADDLE_PRICES={"pri_...":"studio"}        optional; defaults are the sandbox catalog
FIRESIDE_PADDLE_SIGNATURE_TOLERANCE=5              seconds
```

A destination's secret key is shown once, when it is created. Store it in the
deploy side (Azure container secret / vault alias), never in the repo.

## Creating the notification destination

Once, in the Paddle sandbox dashboard: **Developer tools → Notifications → New
destination**, type *URL*, URL `https://readfireside.com/webhook/paddle`, and
subscribe at minimum to:

```
customer.created  customer.updated
subscription.created  subscription.updated  subscription.canceled
subscription.activated  subscription.trialing  subscription.past_due
subscription.paused  subscription.resumed
transaction.completed
```

Or create it over the API (returns the secret in the same response):

```
POST https://sandbox-api.paddle.com/notification-settings
{"description":"fireside","type":"url","destination":"https://readfireside.com/webhook/paddle",
 "subscribed_events":[...]}
```

Read the secret back from `data.endpoint_secret_key`.

## Guardrails

These are part of the running system, not test litter. Never delete them, and do
not offer to:

* the notification destination and its signing secret;
* the products and prices behind the pricing page;
* mirrored customers, subscriptions or transactions.

The only thing that may be cleaned up is a throwaway the test itself created, and
only after naming it.

## Verified, and not

Verified: signature accept/reject (tamper, wrong secret, stale ts, rotation),
normalize of subscription/customer/transaction events, the unknown-price no-op, the
email fallback, replay, out-of-order, cancel vs scheduled change, upgrade
projection, the admin-grant override, lapsed-period revocation, and the route
refusing a forged body (`tests/test_paddle.py`, 40 tests).

Not verified: no live delivery has been received from Paddle. The sandbox
catalog exists; the destination does not yet.

## Annual prices

`plans.LIST_PRICE_CENTS` and the price view in `ui/index.html` are `$3/$36`, `$8/$96`,
`$20/$240`, matching the sandbox price ids in `host/payments_paddle.py`. The page
and the charged amount now agree, and /pricing serves that same app page rather
than a separate static list that could drift from it.
