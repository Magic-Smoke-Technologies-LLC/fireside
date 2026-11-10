# A model review, before voicing, and before trusting an unseen import

Chris, 2026-11-09, in two messages:

> I want the ability to send text to the model before voicing. With an
> estimated cost. I also need admin area to allow it without cost.

> And I think we should keep a table of URLs/sites we have seen. If we are given
> a site that has no plugin, and has never been seen. Then on first import, the
> model runs a check to make sure the import got it right.

This document is the shape. It is written before the code so the two costs are
named in advance instead of discovered in the invoice.

## What it is not

Not a rewrite. The model does not edit prose, fix grammar, or "improve" a
chapter. Its whole job is to answer a yes/no question about whether what we
extracted is what the page says, and to name what is wrong when it is not.

Not a gate that can hide a failure. A review that fails must say so in a place
a person reads, with the reason. A silent drop would be worse than bad text,
because the reader pays for a book and gets nothing.

## Two different jobs, two different prices

**Import review (once per book, not per chapter).** Did we get the right work,
all of it, and no furniture? Input: the book title, the source URL, the first
and last chapter's opening ~400 characters, and the chapter count. Output: a
verdict and a list of concrete complaints ("chapter 12 is a teaser", "every
chapter repeats 'Next chapter →'"). This runs on an unseen host only.

**Pre-voice review (per chapter).** Is this text worth paying to speak? Input:
the chapter's opening and closing ~400 characters plus its length. Output:
verdict plus complaints ("navigation furniture", "no prose", "truncated
mid-sentence"). This runs on every chapter only when the account has turned it
on, or when the import review asked for it.

Both send excerpts, not the whole chapter. A 400-character sample at each end
catches every failure mode we have actually seen, and it makes the cost a tenth
of sending the book.

## The cost, said before it is spent

`estimate(model, chars_in, calls)` -> `{usd, per_book, per_chapter}` from the
provider's own published price per million tokens, live at the moment of the
question. The import screen shows it before the button: "Review this import:
about $0.004, once." The chapter path shows the same per chapter.

The estimate is a band, not a promise: tokens are not characters, and the model
may answer with more than one sentence. The number shown is the ceiling we
would accept, and a run that would exceed it is refused before it starts.

## Who pays, and the admin switch

Three settings, in this order of precedence:

1. **Per account**: `review_mode` = `off` | `import` | `import+chapters`.
   Default `import` only for the first import on an unseen host, otherwise off.
2. **Per server**: `review_provider` names a credential alias. Whoever supplies
   the key pays the provider directly; if the operator supplies it, the operator
   pays.
3. **Admin grant**: an admin can mark an account `review_comped`, which means
   the cost is shown but not charged to the account's balance. It is still spent
   at the provider; it is absorbed here, and it appears in the ledger as an
   absorbed row, not as a free run. Anything else would make the warehouse
   lie.

## The site table

```sql
CREATE TABLE seen_hosts (
  host        VARCHAR(255) PRIMARY KEY,
  first_seen  VARCHAR(32),
  last_seen   VARCHAR(32),
  imports     INT DEFAULT 0,
  reader      VARCHAR(64),     -- which plugin matched, '' when none did
  reviewed    INT DEFAULT 0    -- import reviews run against this host
);
```

A host is the unit, not a URL: `archiveofourown.org` is one site whether the
link came from a tag page, a series, or a work. The rule Chris described:

- a reader plugin matched -> no automatic review. The plugin's own contract is
  the verification.
- no plugin, host already seen and its last review passed -> no review.
- no plugin, host unseen (or its last review failed) -> review the first import
  before any chapter is voiced.

The review's verdict is stored on the book, not only in a log, so a reader can
see why we flagged it.

## What it can and cannot catch

Catches: wrong work behind a redirect, teasers, furniture, truncated chapters,
an index that returned fragments of a different story.

Cannot catch: a machine translation that reads fine, a chapter that is
correct but dull, or a site that serves different text to a bot than to a
browser. For that last one the honest answer is the reader plugin, not the
model.

## Order of work

1. `host/review.py`: estimate, call, verdict, cache by content hash. Tests with
   a fake provider, no network.
2. `seen_hosts`: table, and the read side wired into the import path so the
   decision is made where the import happens.
3. The import screen: the estimate, the verdict, the one button.
4. The chapter path, behind the account setting.
5. The admin screen: provider, comp, and what has been spent.

Nothing in 1-2 is visible; 3 is the first thing a reader meets.