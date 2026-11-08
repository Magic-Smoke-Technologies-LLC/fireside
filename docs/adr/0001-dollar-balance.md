# The balance is dollars, not characters

A user's balance is money. A plan adds a monthly dollar credit to it (for example, $20/mo adds $15), and funds the user adds land in the same balance. Voicing is charged in dollars against it, and each job is settled on a receipt. The balance rolls over, never expires and is never refunded. This replaces the character ledger and the per-plan character allowance (50,000 characters a month on platform). We chose dollars because one unit covers what users pay for and what they spend, across providers that charge different rates. Characters would have to be converted for every provider and would hide the platform fee.

## Consequences

- PRICING.md, `host/accounting.py` and `host/allowance.py` describe the old character model and need to move to dollars.
