# Notices: one event, one row, one bell

A voice run takes minutes on the server. The reader closes the tab, walks to the
library, opens the same book in a second window. Until 2026-11-09 the bell was a
JavaScript array: a conversion that failed after they left was a fact nobody
could learn, and the same failure twice in a session looked like one.

Now the server writes a row and the bell reads it. The row is the in-app channel.

## The shape

`notifications(id, user_id, kind, text, route, dkey, created_at, read_at)`

- **kind**: `job`, `billing`, `wallet`, `account`. An unknown kind is stored as
  `account` rather than refused; a notice is a record, not a schema exercise.
- **route**: where tapping it should land the reader (`#/play/<book>`,
  `#/settings/billing`). Empty means it is information, not a door.
- **dkey**: an idempotency key. The same key on an *unread* row is the same
  event, so a retried write does not raise the bell twice. Once read, the key is
  free again: the next run really is news.
- **read_at**: empty until the reader opens it. Read rows are pruned past 50 per
  account; unread rows are never dropped, because that is the whole point.

## Routes

| route | what |
|---|---|
| `GET /notifications?n=20` | this account's rows, newest first, plus the unread count |
| `POST /notifications/read` | `{"ids": [...]}` for those, or no `ids` for all |

Both take the signed-in account from the session; a user id for somebody else in
the query or body is ignored, as everywhere else on this host.

## What writes a row today

- **A conversion finishing**, while the reader was watching or not. The job row
  is the record of how it ended, so the worker reads it back rather than
  guessing: `done` is "ready to play", `error` carries the job's own reason.
  A run the reader cancelled gets no notice; they did it on purpose.
- **A plan change, a scheduled cancel, and a resume.** These are the money
  events where a person reasonably wants a line they can point at later.

Import is not here. It is synchronous, the reader is watching it, and a notice
for something you just did is noise.

## Channels after this one

The row is the in-app channel; a second channel is a branch at the same call
site (`host/notices.py:notify`), not a second path for every caller. Email is
already possible (`host/mailer.py`) and needs one thing before it is honest: an
opt-in, so a notice does not become mail nobody asked for. A screen-level push
needs the same decision plus a device token, and neither is built.

## What it cost to learn

`read_at` is `DEFAULT ''`, not NULL, which is the house convention for strings
in this schema. `WHERE read_at IS NULL` therefore matched nothing and the bell
counted zero unread rows while the table filled up. Every write looked right and
every count was wrong. The predicates now say `COALESCE(read_at,'')=''`, which
means the same thing on both engines and cannot be defeated by a default.