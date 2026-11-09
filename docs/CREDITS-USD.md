# Credits in dollars (2026-11-09)

A decision doc. It changes the unit of the credit ledger from characters to
USD, and it says exactly which files move and which do not.

## The decision

Chris, 2026-11-09, in four messages:

1. **Prices stand.** Reader $3/$36, Listener $8/$96, Studio $20/$240. The
   design deck's yearly 10x ($30/$80/$200) is not taken.
2. **Credit sales move to dollars.** A user funds any amount they choose, with
   a minimum, so the same balance can later buy other things. Money is easier
   to conceive than characters.
3. **Minimum top-up is $5**, raised from $3. Paddle charges 5% + $0.50 per
   transaction: at $3 that flat fee is 21.7% of the charge ($0.65 of $3), at
   $5 it is 15%, at $10 it is 10%. $3 is exactly where the fixed fee is
   proportionally worst.
4. **The tiers grant credit instead of characters.** Studio grants **$15 per
   paid period**. That is a substitution, not an equivalence: $15 is not
   claimed to buy 1M characters, and the plan page must not imply it does.
5. **The grant rolls.** It is a dollar line item the user already allocated,
   not a use-it-or-lose-it allowance, and not a bet on the Azure rate.
6. **On cancel or downgrade the grants stop; the remaining balance stays
   spendable. No clawback.**

What is unchanged: storage allowances (2 GB / 5 GB / 10 GB), the free tier at
500 MB, BYO-key self-hosting as the zero-margin floor, and the rule that the
sale connection's plugin is the only authority on price.

## Why this is a small change to the code

The host already moves a number without knowing what it counts. The unit
arrives from the plugin and travels with the reservation:

- `plugins/speech/azure/plugin.py` `quote()` returns
  `Quote(price=chars, currency="chars")` and `report()` returns the actual
  count in the same unit. `host/app.py:1476-1520` reads
  `Quote.price`/`Quote.currency` into `accounting.reserve(...)` and settles on
  the plugin's report. Nothing in the host does character arithmetic.
- `accounting.topup/reserve/settle` move `amount` in `currency`. Both store the
  number; neither inspects the unit.
- `balances` is one row per user with a `currency` column, updated on every
  top-up. So a user holds exactly one currency at a time, which is what makes
  the switch a cutover rather than a dual-unit ledger.

So the switch is three edits and a migration, not a redesign.

## The edits

### 1. The sale plugin prices in USD

`quote()` for a sale reservation returns `price = chars_est / 1e6 *
sale_rate_per_1m(cred)` and `currency="USD"`.
`report()` returns `cost = chars_actual / 1e6 * sale_rate_per_1m(cred)`.

Two properties to preserve:

- **The rate is read at quote time and again at report time, and it can move
  between them.** Settling with the report's rate is right for a run that
  started before a price change, but it means the rate used for the charge
  must be the rate that was quoted, so the quote must travel with the job.
  Simplest honest fix: stamp the quoted rate into the reservation note and
  settle against it.
- **Reservations are now small floats.** A 40k-character chapter at $19.63/M is
  $0.785. Round the reserve **up** to the cent and round the settled charge to
  the cent, and never let the rounding favour us on the settlement (the old
  character ledger floored, so the fraction landed on us). In dollars the
  natural floor is one cent.

### 2. The grant becomes a dollar grant

`host/quota.py`: `PLAN_CHARS` → `PLAN_CREDIT_CENTS`
`{free: 0, platform: 100, listener: 250, studio: 1500}`.

The Reader/Listener numbers are the one open decision. The old rule was "each
rung's allowance costs about a third of its price"; a third of $3 is $1, a
third of $8 is $2.50. Studio is deliberately not a third: it is the
buy-characters-outright rung, which is why $15 on $20 is a different kind of
grant. **Pending Chris's number.**

`host/allowance.py`: same shape, one line changes. `topup(..., currency="USD")`
and the entry id stays `allowance:<user>:<period_end>` so a reload or a retried
webhook still cannot stack the grant.

### 3. The label stops claiming characters

- `/credits` returns the dollar balance and the dollar rate, and the bundles
  are dollar-denominated.
- `/plans` prints `$15 credit` where it printed `1M characters`.
- Anything that says "1M characters included" has to go, because it is no
  longer true. The honest line is: "$15 of credit each period, which buys
  about 764k characters at today's rate" - with today's rate read live, never
  hard-coded, and never as a promise.

## The migration, measured today

Live DB at 2026-11-09 10:58Z: **five non-zero balances, every one of them in
`chars`, 363,289 characters total.**

| account | chars |
|---|---|
| local:ren-e2e | 163,289 |
| local:2forkittykat | 50,000 |
| local:cstolworthy | 50,000 |
| local:readfireside.cttoken.ren | 50,000 |
| local:readfireside.demo.ren | 50,000 |

All five are allowance grants or test purchases. Two are real (Cat, Chris), the
rest are mine.

Proposed: **convert once, at the retail rate ($19.63/M), and flip the row's
currency.** 50,000 chars becomes $0.98; the whole set costs $7.13 of notional
value to convert. That is cheap, honest (it is what the characters were worth
that day), and it leaves one unit in the database.

Alternative, if that feels wrong: leave the five rows alone and refuse to
top up in dollars while a chars balance is non-zero. That keeps two units alive
in the code for two real accounts forever. Not worth it.

## What the code does NOT need, and why

`allowance.py`'s comment says the grant "does not roll over". The code has never
done that: `accounting.topup` adds to `balances.amount` and nothing in the host
ever subtracts a lapsed period. So the roll-over behaviour is already what the
database does; the comment was the only thing saying otherwise, and under this
decision the comment is what changes.

## What breaks if this is done carelessly

- **A reservation in dollars and a balance in characters silently compare.**
  `available = balance - reserved` ignores currency. The cutover has to be one
  write: convert every balance row, then serve the new unit. A user who
  renders between the two halves would see 50,000.00 "dollars".
- **A $5 minimum with a $0.79 chapter** means a user topping up for one book
  still keeps a balance. That is the design, not a leak, but the buy screen
  should say the balance stays.
- **Credits are now a liability with a dollar face.** Unspent balance is a
  promise to deliver narration at whatever Azure charges later. It rolls, so it
  cannot be repriced. At the current shelf (12 accounts, none spendy) this is
  cents. Revisit if a user ever holds three figures of balance.

## Can Paddle hold the allowance itself? No, not today (checked 2026-11-09)

Chris asked whether Paddle can meter the plan allowance so we don't have to
count. Read the actual docs, not a blog post.

Paddle Billing does have native usage-based billing:
`usage balance units` (what a customer holds), `meters` (action -> unit at a
rate), `allowances` on prices (granted each billing period), `POST
/usage-events`, `GET /customers/{id}/usage-balances` returning `used`,
`remaining`, `overage`, and a `usage_balance.depleted` webhook.

It is the right shape. It cannot take this job yet, for four reasons in
descending order of severity:

1. **"You can't add allowances to a price with a trial period."** Every one of
   our six prices carries a 7-day trial. That single line disqualifies the
   feature as configured. Either Paddle meters, or we keep trials, not both.
2. **No top-ups.** "Customers get more usage when their subscription renews or
   when they change plan." A $50 load is not expressible. That is exactly the
   dollar wallet we just designed.
3. **Early access (beta).** Application required, acceptance not guaranteed,
   fields and behaviour may change before general release, and Paddle "never
   blocks usage or bills for overage" during beta. So the enforcement decision
   is ours anyway, today.
4. **Period-scoped, not rolling.** Unused usage expires at renewal and never
   rolls. Our balance rolls by Chris's call. Also missing: prorated plan
   changes, batched usage events, any threshold webhook other than depleted.

So: **we keep the count.** `quota.py`/`accounting` stay the meter. Revisit if
Paddle ships GA with trials + top-ups; the seam is already the right shape,
because the host only reads numbers and never does character arithmetic.

### What Paddle does own, and the top-up path

Paddle stays the only door money comes through, including top-ups:

- **Subscription**: create, renew, trial, dunning, card update, cancel, tax,
  invoices, chargebacks. All Paddle. Any plan change sends the customer to the
  customer portal; we do not build an upgrade path.
- **Top-up**: a one-time price (`billing_cycle: null`) charged to the
  subscription with `POST /subscriptions/{id}/charge`, `effective_from:
  immediate`. It collects on the card on file, no second card entry, and gets
  its own receipt. `transaction.completed` on the webhook is the only writer
  that credits the dollar balance.

Two things this needs in code that do not exist yet:

- one-time prices for $5 / $10 / $25 / $50 (a `null` billing cycle, one per
  amount, since Paddle prices are fixed amounts, not arbitrary);
- the webhook handler must distinguish a one-time top-up transaction from a
  subscription transaction before crediting, or a renewal would pay out twice.

A user with no subscription cannot use that path (it bills "to a
subscription"), so the top-up button is only live for subscribed accounts, or
we open a one-off checkout for the same price. Decide later; not blocking.
