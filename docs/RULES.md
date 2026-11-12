# MANDATE rules

MANDATE is a paper-trading record. No real money, broker or exchange account is involved.

## Accounts

- Two separate accounts, `stocks` and `crypto`, each started with $1,000 in cash. Money never moves between them.
- Long only. No borrowing, short selling, options or futures. Fractional units are allowed.
- Stocks account: any US-dollar stock or fund on Yahoo Finance that closed at $5 or more and has a median daily dollar volume of at least $20 million over the prior 20 sessions.
- Crypto account: the coins listed under `crypto.allowed` in `config/settings.json`, priced in US dollars on Coinbase Exchange.
- A buy cannot take one holding past 35% of the account's value at that day's open. An oversized buy is cut down to fit. Orders under $10 are not placed.

## Decisions and fills

- A decision is a new JSON file in `decisions/inbox/`. It can carry any number of orders up to eight, including none.
- The engine stamps each decision with the time it first sees the file. The time written inside the file is never used to qualify a trade.
- An order fills at the opening price of the first completed daily bar that began after that first-seen time. Stock bars begin at 9:30 a.m. New York time. Crypto bars begin at 00:00 UTC.
- Sells are processed before buys on the same day.
- Each fill pays a 0.10% fee and takes a price 0.05% worse than the open.
- An order that cannot fill within six days expires.
- Recorded decisions are never edited or removed.

## Valuation

- Holdings are valued at each day's closing price.
- Stock splits adjust the unit count. Cash dividends are credited on the ex-dividend date.
- Benchmarks: $1,000 in SPY for stocks and $1,000 in BTC for crypto, bought at the first close on the books, with SPY dividends reinvested.
- If a price for a holding or benchmark is unavailable, that account's books wait until it is, so no day is valued with a missing price.

## Limits of the simulation

Real orders can fill at different prices than a daily open, free price feeds can be late or wrong, and short records say little about skill. Nothing here is investment advice.
