# MANDATE

Two paper accounts of $1,000, one for stocks and one for crypto, managed end to end by Claude, an AI model made by Anthropic. Claude does the research, chooses the trades, and writes down its reasons before any order can fill. No real money is involved.

Site: https://prototypeversions.github.io/MANDATE/

## How it works

- **Decisions.** On a schedule, Claude researches the market and adds a decision file to `decisions/inbox/`: stocks on weekday mornings before the US open, crypto every afternoon (Central time). A decision can hold several orders or none.
- **The books.** A GitHub Actions workflow (`.github/workflows/ledger.yml`) runs `scripts/engine.py` several times a day. It collects daily prices, records new decisions with the time it first saw them, fills stock orders at the next market open and crypto orders at the first price of the next UTC day, and values both accounts against their benchmarks.
- **The plan.** `strategy/stocks.md` and `strategy/crypto.md` hold the current plan for each account. Claude can rewrite them, and each change is noted in the journal.
- **The site.** `index.html` reads the JSON files in `data/` and shows the accounts, the journal and the plan.

The rules are in [docs/RULES.md](docs/RULES.md). The procedure each decision run follows is in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Run it yourself

```
python -m unittest discover -s tests   # engine tests, standard library only
python scripts/engine.py               # one update of the books (needs internet access)
```

Prices come from Yahoo Finance and Coinbase Exchange. Nothing here is investment advice.
