"""Record test fixtures from the live public endpoints (one polite request each), trimmed
to the parts the parsers read so the repo stays small.

    python scripts/record_fixtures.py                    # fetch live, write tests/fixtures/
    python scripts/record_fixtures.py --from-raw DIR     # trim previously saved raw responses
    python scripts/record_fixtures.py --nba-books        # NBA fixtures for the seven added books

Raw files are named fanduel_<league>.json, draftkings_<league>.json, pinnacle_<league>_matchups.json,
pinnacle_<league>_markets.json, espn_<league>.json and, optionally, one ESPN scoreboard
per day as espn_<league>_day_<YYYYMMDD>.json (merged into espn_<league>_schedule.json).
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oddsapi.adapters.fanduel import MARKET_TYPES  # noqa: E402
from oddsapi.config import LEAGUES, get_settings  # noqa: E402
from oddsapi.http import FetchError, PoliteClient  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def trim_fanduel(raw: dict) -> dict:
    att = raw.get("attachments") or {}
    markets = att.get("markets") or {}
    keep = {k: m for k, m in markets.items() if m.get("marketType") in MARKET_TYPES}
    keep.update(dict([kv for kv in markets.items() if kv[0] not in keep][:3]))  # a few non-game markets to prove filtering
    event_ids = {str(m.get("eventId")) for m in keep.values()}
    events = {k: e for k, e in (att.get("events") or {}).items() if k in event_ids}
    return {"attachments": {"events": events, "markets": keep}}


def trim_pinnacle(matchups: list, markets: list) -> dict:
    kept = [m for m in matchups if m.get("type") == "matchup"] + [m for m in matchups if m.get("type") != "matchup"][:3]
    ids = {m["id"] for m in kept}
    mk = [x for x in markets if x.get("matchupId") in ids and x.get("period") in (0, 1)]
    return {"matchups": kept, "markets": mk}


def trim_draftkings(raw: dict) -> dict:
    from oddsapi.adapters.draftkings import MARKET_NAMES

    def name(m):
        return ((m.get("marketType") or {}).get("name") or m.get("name") or "").strip().lower()

    markets = [m for m in raw.get("markets") or [] if name(m) in MARKET_NAMES]
    markets += [m for m in raw.get("markets") or [] if name(m) not in MARKET_NAMES][:3]
    market_ids = {str(m["id"]) for m in markets}
    event_ids = {str(m.get("eventId")) for m in markets}
    return {
        "events": [e for e in raw.get("events") or [] if str(e.get("id")) in event_ids],
        "markets": markets,
        "selections": [s for s in raw.get("selections") or [] if str(s.get("marketId")) in market_ids],
    }


def trim_espn(raw: dict) -> dict:
    def comp(c):
        return {
            "status": c.get("status"),
            "venue": {"fullName": (c.get("venue") or {}).get("fullName")},
            "competitors": [
                {
                    "homeAway": t.get("homeAway"),
                    "score": t.get("score"),
                    "linescores": [{"value": x.get("value")} for x in t.get("linescores") or []] if "linescores" in t else None,
                    "team": {k: t["team"].get(k) for k in ("id", "displayName", "abbreviation", "shortDisplayName", "name", "location")},
                }
                for t in c.get("competitors") or []
            ],
        }

    events = [
        {"id": e["id"], "date": e["date"], "name": e.get("name"), "season": e.get("season"), "competitions": [comp(e["competitions"][0])]}
        for e in raw.get("events") or []
    ]
    for e in events:
        for t in e["competitions"][0]["competitors"]:
            if t["linescores"] is None:
                del t["linescores"]
    return {"season": raw.get("season"), "day": raw.get("day"), "events": events}


def _pick(d: dict, keys) -> dict:
    return {k: d[k] for k in keys if k in d}


def trim_betrivers(raw: dict) -> dict:
    from oddsapi.adapters.betrivers import _market

    items = []
    for ev in raw.get("items") or []:
        offers = [bo for bo in ev.get("betOffers") or [] if _market(bo)] + [bo for bo in ev.get("betOffers") or [] if not _market(bo)][:1]
        items.append({
            **_pick(ev, ("id", "name", "start", "state", "participants")),
            "betOffers": [
                {**_pick(bo, ("betDescription", "betOfferType", "status")),
                 "outcomes": [_pick(o, ("type", "oddsAmerican", "line", "status", "label")) for o in bo.get("outcomes") or []]}
                for bo in offers
            ],
        })
    return {"items": items}


def trim_bovada(raw: dict) -> dict:
    groups = []
    for g in raw.get("groups") or []:
        events = []
        for ev in g.get("events") or []:
            dgs = [
                {"description": dg.get("description"), "markets": [
                    {**_pick(m, ("key", "description", "status", "period")),
                     "outcomes": [_pick(o, ("description", "type", "status", "price")) for o in m.get("outcomes") or []]}
                    for m in dg.get("markets") or []
                ]}
                for dg in (ev.get("displayGroups") or [])[:2]
            ]
            events.append({**_pick(ev, ("id", "description", "startTime", "live", "competitors")), "displayGroups": dgs})
        groups.append({"path": g.get("path"), "events": events})
    return {"groups": groups}


def trim_kambi(raw: dict) -> dict:
    from oddsapi.adapters.kambi import CRITERIA

    offers = {}
    for eid, off in (raw.get("offers") or {}).items():
        kept, per = [], {}
        for bo in off.get("betOffers") or []:
            label = (bo.get("criterion") or {}).get("englishLabel")
            if label not in CRITERIA:
                continue
            main = "MAIN_LINE" in (bo.get("tags") or [])
            if not main and per.get(label, 0) >= 2:
                continue
            per[label] = per.get(label, 0) + (0 if main else 1)
            kept.append({
                "criterion": {"englishLabel": label}, "tags": bo.get("tags"),
                "outcomes": [_pick(o, ("type", "participantId", "oddsAmerican", "line", "status", "englishLabel")) for o in bo.get("outcomes") or []],
            })
        events = [{**_pick(e, ("id", "homeName", "awayName", "start", "state")), "participants": e.get("participants")} for e in off.get("events") or []]
        offers[eid] = {"events": events, "betOffers": kept}
    listing = {"events": [{"event": _pick(i.get("event") or {}, ("id", "homeName", "awayName", "start", "state"))} for i in (raw.get("list") or {}).get("events") or []]}
    return {"list": listing, "offers": offers}


def trim_kalshi(raw: dict) -> dict:
    keys = ("ticker", "yes_sub_title", "floor_strike", "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars", "status")
    return {
        market: [{"event_ticker": e["event_ticker"], "title": e.get("title"), "markets": [_pick(m, keys) for m in e.get("markets") or []]} for e in events]
        for market, events in raw.items()
    }


def trim_polymarket(raw: dict) -> dict:
    from oddsapi.adapters.polymarket import GAME_SLUG, TYPES

    keys = ("sportsMarketType", "question", "line", "outcomes", "outcomePrices", "bestBid", "bestAsk", "active", "closed", "acceptingOrders")
    events = []
    for ev in raw.get("events") or []:
        game = bool(GAME_SLUG.match(ev.get("slug", "")))
        if not game and len(events) and sum(1 for e in events if not GAME_SLUG.match(e["slug"])) >= 2:
            continue
        markets = [_pick(m, keys) for m in ev.get("markets") or [] if m.get("sportsMarketType") in TYPES]
        events.append({**_pick(ev, ("slug", "title", "startTime", "closed")), "markets": markets if game else markets[:2]})
    return {"events": events}


def trim_matchbook(raw: dict) -> dict:
    nba = [e for e in raw.get("events") or [] if any(t.get("name") == "NBA" for t in e.get("meta-tags") or [])]
    other = [e for e in raw.get("events") or [] if e not in nba][:2]
    out = []
    for e in nba + other:
        markets = [
            {**_pick(m, ("name", "market-type", "status", "handicap")),
             "runners": [{**_pick(r, ("name", "handicap", "status")), "prices": [_pick(p, ("side", "decimal-odds", "odds")) for p in r.get("prices") or []]} for r in m.get("runners") or []]}
            for m in e.get("markets") or [] if m.get("market-type") in ("money_line", "handicap", "total")
        ]
        out.append({**_pick(e, ("id", "name", "start", "in-running-flag", "meta-tags")), "markets": markets})
    return {"events": out}


def trim_smarkets(raw: dict) -> dict:
    markets = [_pick(m, ("id", "event_id", "name", "market_type", "state")) for m in raw.get("markets") or []]
    ids = {m["id"] for m in markets}
    contracts = [_pick(c, ("id", "market_id", "name", "contract_type")) for c in raw.get("contracts") or [] if c.get("market_id") in ids]
    cids = {str(c["id"]) for c in contracts}
    quotes = {k: {"bids": v.get("bids", [])[:1], "offers": v.get("offers", [])[:1]} for k, v in (raw.get("quotes") or {}).items() if k in cids}
    events = [_pick(e, ("id", "name", "start_datetime", "full_slug", "state")) for e in raw.get("events") or []]
    return {"events": events, "markets": markets, "contracts": contracts, "quotes": quotes}


NEW_NBA_BOOKS = {
    "betrivers": trim_betrivers,
    "bovada": trim_bovada,
    "unibet": trim_kambi,
    "kalshi": trim_kalshi,
    "polymarket": trim_polymarket,
    "matchbook": trim_matchbook,
    "smarkets": trim_smarkets,
}


def record_nba_books(now: str) -> None:
    """One fetch per new book for NBA, plus ESPN scoreboards for today and tomorrow (ET)
    as espn_nba_slate.json so the fixtures can be matched to games."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from oddsapi.adapters import ADAPTERS
    from oddsapi.scores.espn import PATHS, URL

    settings = get_settings()
    client = PoliteClient(settings)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    events = []
    for d in (today, today + timedelta(days=1)):
        events += trim_espn(client.get_json(URL.format(path=PATHS["nba"]), params={"dates": d.strftime("%Y%m%d")}))["events"]
    write("espn_nba_slate.json", {"events": events}, now)
    for book, trim in NEW_NBA_BOOKS.items():
        try:
            write(f"{book}_nba.json", trim(ADAPTERS[book](client, settings).fetch_raw("nba")), now)
        except FetchError as e:
            print(f"skip {book}: {e}")


def write(name: str, data, recorded_at: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"_recorded_at": recorded_at, "data": data}
    (OUT / name).write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {name} ({(OUT / name).stat().st_size // 1024} KB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-raw", type=Path)
    ap.add_argument("--recorded-at", help="ISO timestamp to store with --from-raw fixtures")
    ap.add_argument("--nba-books", action="store_true", help="record NBA fixtures for the seven non-original books")
    args = ap.parse_args()
    now = args.recorded_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.nba_books:
        record_nba_books(now)
        return

    if args.from_raw:
        load = lambda n: json.loads((args.from_raw / n).read_text())  # noqa: E731
        for lg in LEAGUES:
            write(f"fanduel_{lg}.json", trim_fanduel(load(f"fanduel_{lg}.json")), now)
            write(f"pinnacle_{lg}.json", trim_pinnacle(load(f"pinnacle_{lg}_matchups.json"), load(f"pinnacle_{lg}_markets.json")), now)
            write(f"espn_{lg}.json", trim_espn(load(f"espn_{lg}.json")), now)
            if (args.from_raw / f"draftkings_{lg}.json").exists():
                write(f"draftkings_{lg}.json", trim_draftkings(load(f"draftkings_{lg}.json")), now)
            days = sorted(args.from_raw.glob(f"espn_{lg}_day_*.json"))
            if days:
                events = [e for d in days for e in trim_espn(json.loads(d.read_text()))["events"]]
                write(f"espn_{lg}_schedule.json", {"events": events}, now)
        return

    settings = get_settings()
    client = PoliteClient(settings)
    from oddsapi.adapters import DraftKingsAdapter, FanDuelAdapter, PinnacleAdapter
    from oddsapi.scores.espn import PATHS, URL

    fd, pn, dk = FanDuelAdapter(client, settings), PinnacleAdapter(client, settings), DraftKingsAdapter(client, settings)
    for lg in LEAGUES:
        for name, fn in (
            (f"fanduel_{lg}.json", lambda: trim_fanduel(fd.fetch_raw(lg))),
            (f"draftkings_{lg}.json", lambda: trim_draftkings(dk.fetch_raw(lg))),
            (f"pinnacle_{lg}.json", lambda: trim_pinnacle(**pn.fetch_raw(lg))),
            (f"espn_{lg}.json", lambda: trim_espn(client.get_json(URL.format(path=PATHS[lg]), params={"dates": date.today().strftime("%Y%m%d")}))),
        ):
            try:
                write(name, fn(), now)
            except FetchError as e:
                print(f"skip {name}: {e}")


if __name__ == "__main__":
    main()
