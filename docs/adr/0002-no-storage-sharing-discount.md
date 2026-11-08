# Every holder pays for a book's full storage

When two users hold the same book, each pays for its full size against their own storage allowance, even though the server keeps one copy of the bytes. Each user gets their own copy of the book on their devices, so each one pays for it. Counting shared copies and releasing storage automatically was more code and more ways to get it wrong, and it saved nobody anything in the common case. Content addressing still keeps one copy on the server; this decision is only about billing.

## Consequences

- Moving a book into a shared catalog so it stops counting against a user is a separate feature, not yet built.
