"""Record test fixtures from the live public endpoints (one polite request each), trimmed
to the parts the parsers read so the repo stays small.

    python scripts/record_fixtures.py                    # fetch live, write tests/fixtures/
    python scripts/record_fixtures.py --from-raw DIR     # trim previously saved raw responses

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


def write(name: str, data, recorded_at: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"_recorded_at": recorded_at, "data": data}
    (OUT / name).write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {name} ({(OUT / name).stat().st_size // 1024} KB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-raw", type=Path)
    ap.add_argument("--recorded-at", help="ISO timestamp to store with --from-raw fixtures")
    args = ap.parse_args()
    now = args.recorded_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
