# sportsbook-odds-api (prototype)

A small read API and web board for **NBA, NFL and MLB** schedules, live scores and
**pregame odds** (moneyline, spread / run line, total). FanDuel, DraftKings and Pinnacle
cover all three leagues; seven more sources (BetRivers, Unibet, Bovada, Matchbook,
Smarkets, Kalshi, Polymarket) cover the NBA, polled on a day-slate schedule.

A private worker reads each source's public, logged-out JSON (the same endpoints
their own web pages call) and ESPN's public scoreboard, normalizes everything into
SQLite, and a FastAPI app serves it, plus a browser board at `/ui`. Request handlers
never call an upstream.

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
- Bovada is an offshore book, not licensed in any US state or in Ontario. Showing its
  prices next to licensed books is promotion of an unlicensed operator in most of those
  places. It is in the prototype because it has a public feed; drop it
  (`ODDSAPI_BOOKS`) before anything leaves the team.
- Kalshi, Polymarket, Matchbook and Smarkets are exchanges or prediction markets, not
  sportsbooks. Their market-data APIs are public, but each has its own API terms
  (attribution, no resale). Their prices here are converted from contract asks or exchange
  back prices and exclude fees and commission, so they are not like-for-like with a
  book's posted price.
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

### The ten NBA sources (first live poll 2026-11-23 02:57 UTC, Monday's 10-game slate)

| Book (id) | Kind | Endpoint read (logged out) | NBA games returned |
| --- | --- | --- | --- |
| FanDuel (`fanduel`) | US sportsbook | as above | 10 |
| DraftKings (`draftkings`) | US sportsbook | as above | 9 |
| Pinnacle (`pinnacle`) | sportsbook (offshore/Curaçao; licensed in Ontario) | as above | 5 (lists only games near tip-off) |
| BetRivers (`betrivers`) | US sportsbook (Rush Street, Kambi-traded) | `nj.betrivers.com/api/service/sportsbook/offering/listview/events?cageCode=2&type=prematch&groupId=1000093652` (pageSize ≤ 20) | 9 |
| Unibet (`unibet`) | sportsbook (Kambi) | `eu-offering-api.kambicdn.com/offering/v2018/ubca/listView/basketball/nba/all/all/matches.json`, then `/betoffer/event/<id>.json` per game for main spread/total | 9 |
| Bovada (`bovada`) | offshore sportsbook | `www.bovada.lv/services/sports/event/v2/events/A/description/basketball/nba?preMatchOnly=true` | 6 |
| Matchbook (`matchbook`) | betting exchange | `api.matchbook.com/edge/rest/events?sport-ids=4&include-prices=true` | 8 |
| Smarkets (`smarkets`) | betting exchange | `api.smarkets.com/v3/events/…`, `/events/<ids>/markets/`, `/markets/<ids>/contracts/`, `/markets/<ids>/quotes/` | 10 |
| Kalshi (`kalshi`) | CFTC-regulated event exchange | `api.elections.kalshi.com/trade-api/v2/events?series_ticker=KXNBAGAME` (and `KXNBASPREAD`, `KXNBATOTAL`) | 10 on the slate (28 listed through the week) |
| Polymarket (`polymarket`) | prediction market | `gamma-api.polymarket.com/events?series_id=10345&closed=false` | 10 on the slate (26 listed) |

BetRivers and Unibet are both traded by Kambi and often post identical prices; they are
kept as separate books because their limits, promos and availability differ, but
treat them as one opinion when judging consensus.

Exchanges (Matchbook, Smarkets, Kalshi, Polymarket) list many spread/total lines per game.
The adapter keeps the one trading closest to 50/50 as the "main" line, and skips any
market whose two sides together cost more than 115% (`MAX_EXCHANGE_OVERROUND`), so an
empty order book (1c bid / 99c ask) can never look balanced. Kalshi tickers carry only a
date (`KXNBAGAME-26NOV23PORPHI`, away team first), so Kalshi games match on teams + ET
date, and are dropped once ESPN shows the game started.

### Books requested but blocked or without a public feed (one request each, honest UA)

| Book | What the VM got | Replaced by |
| --- | --- | --- |
| BetMGM | 403 (Akamai) on the sports API | Unibet (Kambi) |
| Caesars | 403 | BetRivers |
| bet365 | 403 | Matchbook |
| BetOnline | 403 | Bovada (also offshore) |
| Fanatics | no logged-out JSON endpoint found from its public pages; not attempted further | Smarkets |
| ESPN BET / theScore Bet | espnbet.com now fails TLS hostname checks; theScore's web app reads a GraphQL API with persisted queries, which would mean replaying its private app protocol | Kalshi |
| Hard Rock Bet | GraphQL endpoint answers GET with 405; using it means reverse-engineering POST queries | Polymarket |
| Also tried: PointsBet Ontario (Cloudflare challenge), Sports Interaction (403), Bet99 (403), Betano.ca (403), MyBookie (Cloudflare challenge), BetUS (406), BetVictor (451), Betfred (Cloudflare), Kambi `rsiusnj` offering (429 "No access", left alone) | | |

None of these were retried with other headers, a browser, or a different IP.

### Ontario

| Book | Ontario difference |
| --- | --- |
| FanDuel | Separate host: `ODDSAPI_FANDUEL_STATE=on` reads `sbapi.on.sportsbook.fanduel.ca` (checked: 200). |
| DraftKings | Separate site code: `ODDSAPI_DRAFTKINGS_SITE=dkcaon` (checked: 200). |
| BetRivers | Separate site: `ODDSAPI_BETRIVERS_HOST=on.betrivers.ca ODDSAPI_BETRIVERS_CAGE=249` (cage code read from the public page config, not polled). |
| Unibet | Default offering `ubca` / market `CA` is already the Canadian site. US prices would need a US Kambi offering, and the obvious one (`rsiusnj`) refuses anonymous access. |
| Pinnacle | Ontario users get pinnacle.ca (iGaming Ontario licensed); the guest API used here is the global one. Prices usually match; limits differ. |
| Bovada | Not available or licensed in Ontario (Bodog is the Canadian brand, also unlicensed there). |
| Kalshi, Polymarket | Not available to Ontario residents. |
| Matchbook, Smarkets | UK-licensed exchanges, not licensed in Ontario. |
| theScore Bet, PointsBet, BetMGM, Caesars, bet365, Sports Interaction, Bet99, Betano | Ontario-licensed, but all were blocked or had no public feed (table above). |

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

### NBA board (scheduler + web UI)

```bash
export ODDSAPI_CONTACT="you@example.com" ODDSAPI_DB=./oddsapi.sqlite3
export ODDSAPI_LEAGUES=nba                 # only the NBA slate; drop to also poll NFL/MLB
export ODDSAPI_SLATE_REFRESH_SECONDS=1800  # reload today's ET NBA games (default 30 min)
export ODDSAPI_NBA_ODDS_POLL_SECONDS=1800  # poll every book's NBA odds (default 30 min; 60 s floor)
export ODDSAPI_SLATE_DAYS=1                # 2 = today and tomorrow (useful late evening ET)
python -m oddsapi.worker &                 # terminal 1 (or tmux)
uvicorn oddsapi.api:app_factory --factory --host 0.0.0.0 --port 8000   # terminal 2
python -m oddsapi.cli create-key --name "Board" --tier developer --scopes scores:read,odds:read
open "http://localhost:8000/ui#key=sk_live_..."   # or paste the key into the page
```

Every `ODDSAPI_SLATE_REFRESH_SECONDS` the worker fetches ESPN's scoreboard for today in
US Eastern time (plus `ODDSAPI_SLATE_DAYS - 1` more days) and upserts the games. While any
of those games is still pregame, each book's NBA feed is polled every
`ODDSAPI_NBA_ODDS_POLL_SECONDS`; once the last game tips, NBA odds polling stops until the
next slate. Each poll replaces current prices and writes `price_snapshots` rows for every
line or price that moved, exactly as for the other leagues. Backoff, the host gap and the
60 s floor still apply.

`/ui` is a static page that calls `/v1/books`, `/v1/odds` and `/v1/games` with the key you
give it (kept in the browser's localStorage; a `#key=` fragment is never sent to the server).
It shows the day's NBA games with books as columns, highlights the best available price
per row (more points first, then the longer price; stale prices excluded), shows when each
book was last captured and when odds last changed, and refreshes every 60 s
(`/ui?refresh=30` to change). Use Today / Tomorrow or the date picker for other days.

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
  `ODDSAPI_ODDS_MAX_AGE_SECONDS` (600), or after 1.5x and 2.5x the league's poll interval
  if that is longer (NBA on the 30-minute slate: stale after 45 min, omitted after 75).
  A live score is `stale` after 120 s. Finals never go stale.
- `GET /v1/books` lists all ten books; `books=` accepts any of their ids.
- MLB game detail includes `linescore` (runs per inning) when ESPN sends it.

## Ingestion

| Job | Cadence | Notes |
| --- | --- | --- |
| `schedule:<league>` | every 6 h | ESPN, yesterday through +14 days, one request per date. Upserts teams, aliases, games. |
| `scores:<league>` | 30 s while any game is live; 5 min if one starts within 2 h; otherwise none | ESPN, yesterday + today. |
| `slate:nba` | 30 min (`ODDSAPI_SLATE_REFRESH_SECONDS`) | ESPN, today in ET (+ `ODDSAPI_SLATE_DAYS - 1`). |
| `odds:<book>:nba` | 30 min (`ODDSAPI_NBA_ODDS_POLL_SECONDS`) | Only while a slate game is pregame. |
| `odds:<book>:nfl`, `:mlb` | 60 s (floor) | Skipped when the league has nothing scheduled in 72 h. |

Leagues on the slate schedule are set by `ODDSAPI_SLATE_LEAGUES` (default `nba`).

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
  adapters/      base.py (OddsAdapter interface), fanduel.py, draftkings.py, pinnacle.py,
                 betrivers.py, kambi.py (Unibet), bovada.py, matchbook.py, smarkets.py,
                 kalshi.py, polymarket.py
  static/        index.html (the /ui board)
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

The seven added books have NBA fixtures recorded 2026-11-23 02:44 UTC with the ESPN slate
for that day and the next (`espn_nba_slate.json`); `tests/test_new_books.py` pins one game's
prices per book and checks shape, matching and exchange line selection, and
`tests/test_slate.py` covers the 30-minute scheduler. Re-record with
`python scripts/record_fixtures.py --nba-books`.

## Known gaps

- Sandbox "odds delayed 5 minutes" from the plan's tier table is not implemented.
- The rate limiter is in-process memory; multiple API processes need Redis or similar.
- No HTTPS termination; put it behind a proxy that does TLS.
- Pinnacle's guest API only lists a game pregame; most of an NBA slate disappears once it tips.
- FanDuel is read from the New Jersey host (`ODDSAPI_FANDUEL_STATE=nj`); DraftKings from `dkusnj`.
  Lines can differ slightly by state.
- The seven added books are NBA-only adapters. BetRivers, Unibet, Bovada, Matchbook and
  Smarkets list NFL too and would need league ids added; Kalshi and Polymarket have NFL series.
- Exchange prices ignore fees/commission, and thin exchange books can show wide prices
  (e.g. Smarkets -119/-127 on a spread); they rarely win "best price" but are shown as-is.
