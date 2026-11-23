# sportsbook-odds-api (prototype)

A small read API for **NBA, NFL and MLB** schedules, live scores and **pregame odds**
(moneyline, spread / run line, total) from **FanDuel, DraftKings and Pinnacle**.

A private worker reads each sportsbook's public, logged-out JSON (the same endpoints
their own web pages call) and ESPN's public scoreboard, normalizes everything into
SQLite, and a FastAPI app serves it. Request handlers never call an upstream.

Data model, endpoints and ingestion follow `docs/api-plan.md` from the project store,
with one deliberate change: odds come from scrapers instead of a licensed feed. Read the
legal section before showing this to anyone outside the team.

## Legal caveat: read this first

**Do not redistribute scraped odds.** This prototype is for internal evaluation only.

- Sportsbook prices, as displayed, belong to the book. FanDuel's, DraftKings' and
  Pinnacle's terms of use restrict automated access and reuse of site content.
  Pinnacle's terms specifically require a separate written agreement for automated
  access and bar redistribution without approval. Reading a public endpoint
  does not grant a license to republish what it returns.
- Serving these prices to other developers through an API is redistribution. The
  design plan (`docs/api-plan.md`) explains why even licensed aggregators' standard
  terms forbid that without a written rider, and why the plan itself said not to
  scrape. Scraped data is weaker than that, not stronger.
- ESPN's scoreboard JSON is an undocumented public endpoint under ESPN's site terms,
  which restrict commercial reuse. It is a prototype score source, not a production
  license. League data rights for a betting-adjacent product are a separate contract.
- Odds display is regulated advertising in some US states. Get counsel's sign-off
  before any external user, key or public page sees these numbers.
- Each odds source is an adapter behind one interface (`OddsAdapter.fetch(league)`),
  so a licensed feed (The Odds API, SportsGameOdds, Sportradar, Pinnacle's bespoke
  API) can replace a scraper without touching ingestion or the API.

This is a product constraint summary, not legal advice.

## What returned live data from the dev VM (2026-11-23, AWS us-east-1)

| Source | Endpoint read | Result |
| --- | --- | --- |
| FanDuel | `sbapi.nj.sportsbook.fanduel.com/api/content-managed-page?page=CUSTOM&customPageId=<league>` | **Live.** NBA 9 games, NFL 15 games. MLB: offseason, no game markets. |
| DraftKings | `sportsbook-nash.draftkings.com/api/sportscontent/dkusnj/v1/leagues/<id>` | **Live through this app's httpx client.** NBA 7 pregame games, NFL 17 games. MLB: futures only. Note: the same URL returns Akamai 403 "Access Denied" to `curl` (any User-Agent), so DraftKings filters by client. Nothing here imitates a browser. If DraftKings starts returning 403 to this client too, the worker backs off; leave it blocked. |
| Pinnacle | `guest.api.arcadia.pinnacle.com/0.1/leagues/<id>/matchups` and `/markets/straight` | **Live**, no key or header needed. NBA 1 pregame game (the rest of the slate was in play), NFL 7 games. MLB: offseason. |
| ESPN scores | `site.api.espn.com/apis/site/v2/sports/<sport>/<league>/scoreboard?dates=YYYYMMDD` | **Live.** One date per request (ranges return 400). ESPN's CDN returns 403 when the User-Agent contains the word "scraper", so the default UA does not include it. |

MLB market type names on FanDuel (`RUN_LINE`, `TOTAL_RUNS_(OVER/UNDER)`) and DraftKings
(`Run Line`) are best guesses because MLB had no game markets when this was built.
Check them on opening day.

## Scraping rules this code follows

- Only logged-out public JSON the books' own pages request. No login, cookies, session
  replay, CAPTCHA solving, proxy rotation, browser/TLS fingerprint imitation or other
  bot-detection evasion. `PoliteClient` sends one plain GET with no cookie jar and does
  not follow redirects.
- Clear User-Agent: `sportsbook-odds-api-prototype/0.1 (+contact: $ODDSAPI_CONTACT)`.
  Set `ODDSAPI_CONTACT` to a real email before running.
- One odds poll per book per league at most every `ODDSAPI_ODDS_POLL_SECONDS` (default
  and floor: 60 s; going lower requires `ODDSAPI_ALLOW_FAST_POLL=1`). At least
  `ODDSAPI_HOST_GAP_SECONDS` (3 s) between any two requests to the same host. Leagues
  with no scheduled games in the next 72 h are not polled at all (e.g. MLB in November).
- Backoff per job: errors and 5xx double from 2 min to a 1 h cap. A 429 waits at least
  `Retry-After`. A 401/403 is treated as "blocked": 6 h, doubling to a 24 h cap, logged
  and recorded in `poll_status`. There are no in-request retries.
- The FanDuel `_ak` query value is the public application key FanDuel's own site sends
  with every logged-out page view; it is not an account credential. Configurable as
  `ODDSAPI_FANDUEL_AK` in case FanDuel rotates it.

## Setup

Python 3.11+.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export ODDSAPI_CONTACT="you@example.com"     # goes in the User-Agent
export ODDSAPI_DB=./oddsapi.sqlite3

python -m oddsapi.cli init-db
python -m oddsapi.cli probe                   # one polite fetch per book/league, prints what came back
python -m oddsapi.worker --once               # schedule + scores + one odds poll (≈2.5 min: one ESPN request per day)
python -m oddsapi.worker                      # or run forever
python -m oddsapi.cli status                  # last success / failure / backoff per job

python -m oddsapi.cli create-key --name "Me" --tier developer --scopes scores:read,odds:read
uvicorn oddsapi.api:app_factory --factory --port 8000
```

```bash
KEY=sk_live_...   # printed once by create-key
curl -H "Authorization: Bearer $KEY" "localhost:8000/v1/games?league=nba&on=2026-11-22"
curl -H "Authorization: Bearer $KEY" "localhost:8000/v1/games/<id>/odds?books=pinnacle,fanduel"
curl -H "Authorization: Bearer $KEY" "localhost:8000/v1/odds?league=nfl&on=2026-11-29&markets=spread"
```

Run the worker and the API as two processes against the same SQLite file (WAL mode).

## API

As in the plan: `GET /v1/leagues`, `/v1/books`, `/v1/games?league=&on=&time_zone=&status=`,
`/v1/games/{id}`, `/v1/games/{id}/odds?books=&markets=`, `/v1/odds?league=&on=&books=&markets=`.
Interactive docs at `/docs`.

- Errors: `{"error": {"code": "...", "message": "..."}}`. Missing/invalid query, unknown
  league, book or market is 400; unknown game 404; missing/revoked key 401; missing
  scope or a sandbox key outside its league 403; over limit 429 with `Retry-After`.
- Bearer keys are random, shown once, stored as SHA-256. Scopes `scores:read`, `odds:read`.
  Tiers and limits as in the plan (sandbox 60/min + 1,000/day, developer 300/min +
  50,000/day, production 1,200/min). Every authenticated response has `X-RateLimit-*`.
- Both sides of every market are returned with their own line and price. `side` is a
  team id (`t_nba_bos`) for moneyline/spread and `over`/`under` for totals. American
  odds are integers; decimal is a 2-place string.
- `captured_at` is when the worker last saw the price (the books do not timestamp prices).
  A market is `stale: true` after `ODDSAPI_ODDS_STALE_SECONDS` (180) and omitted after
  `ODDSAPI_ODDS_MAX_AGE_SECONDS` (600). A live score is `stale` after 120 s. Finals never go stale.
- MLB game detail includes `linescore` (runs per inning) when ESPN sends it.

## Ingestion

| Job | Cadence | Notes |
| --- | --- | --- |
| `schedule:<league>` | every 6 h | ESPN, yesterday through +14 days, one request per date. Upserts teams, aliases, games. |
| `scores:<league>` | 30 s while any game is live; 5 min if one starts within 2 h; otherwise none | ESPN, yesterday + today. |
| `odds:<book>:<league>` | 60 s (floor) | Skipped when the league has nothing scheduled in 72 h. |

Matching: book team names resolve through an alias table seeded from ESPN (full name,
abbreviation, nickname, and suffixes, so "GS Warriors" finds the Warriors), then the
game is found by league + home + away + start time within 15 minutes (books post start
times a few minutes off ESPN's). Upstream ids are kept in `game_odds_ids`; public ids are
UUIDs. Current prices are replaced per (game, book, market) on each successful poll, and
`price_snapshots` gets a row only when a side's price or line changes. Pregame only:
started/in-play events are dropped by every adapter.

## Layout

```
oddsapi/
  adapters/      base.py (OddsAdapter interface), fanduel.py, draftkings.py, pinnacle.py
  scores/espn.py ESPN scoreboard source
  http.py        PoliteClient (UA, host gap, error mapping) and Backoff
  ingest.py      alias matching, upserts, price snapshots
  worker.py      job scheduler
  api.py         FastAPI app
  auth.py        API keys, scopes, rate limiter
  db.py          SQLite schema
  cli.py         init-db, create-key, revoke-key, status, probe
scripts/record_fixtures.py   re-record trimmed fixtures from the live endpoints
tests/fixtures/              real responses recorded 2026-11-23 (trimmed)
```

## Replacing a scraper with a licensed feed

Write a class with `book`, `source` and `fetch(league) -> list[BookGame]` that returns
the feed's pregame moneyline/spread/total per game (team names, start time, sides
`home`/`away`/`over`/`under`, American price, line from that side's point of view), and
register it in `oddsapi/adapters/__init__.py`. Nothing else changes.

## Tests

```bash
python -m pytest -q
```

Parsers run against real responses recorded from the VM on 2026-11-23 (`tests/fixtures/`,
trimmed by `scripts/record_fixtures.py`). Other tests cover matching, snapshots, the HTTP
error mapping and backoff, the worker's poll cadence and blocked-book handling, and every
API route. No test touches the network. To refresh fixtures: `python scripts/record_fixtures.py`.

## Known gaps

- Sandbox "odds delayed 5 minutes" from the plan's tier table is not implemented.
- The rate limiter is in-process memory; multiple API processes need Redis or similar.
- No HTTPS termination; put it behind a proxy that does TLS.
- Pinnacle's guest API only lists a game pregame; most of an NBA slate disappears once it tips.
- FanDuel is read from the New Jersey host (`ODDSAPI_FANDUEL_STATE=nj`); DraftKings from `dkusnj`.
  Lines can differ slightly by state.
