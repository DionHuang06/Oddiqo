"""Operator commands.

    python -m oddsapi.cli init-db
    python -m oddsapi.cli create-key --name "Acme" --tier developer --scopes scores:read,odds:read
    python -m oddsapi.cli revoke-key --name "Acme"
    python -m oddsapi.cli status
    python -m oddsapi.cli probe [--books fanduel,pinnacle] [--leagues nba]
"""

import argparse
import sys
from datetime import datetime, timezone

from .adapters import build_adapters
from .auth import create_key
from .config import get_settings
from .db import connect, init_db, iso
from .http import FetchError, PoliteClient


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    ck = sub.add_parser("create-key")
    ck.add_argument("--name", required=True)
    ck.add_argument("--tier", default="developer", choices=["sandbox", "developer", "production"])
    ck.add_argument("--scopes", default="scores:read,odds:read")
    ck.add_argument("--league", choices=["nba", "nfl", "mlb"])
    rk = sub.add_parser("revoke-key")
    rk.add_argument("--name", required=True)
    sub.add_parser("status")
    pr = sub.add_parser("probe", help="One polite fetch per book and league; prints what came back. Writes nothing.")
    pr.add_argument("--books")
    pr.add_argument("--leagues")
    args = p.parse_args(argv)

    settings = get_settings()
    if args.cmd == "probe":
        client = PoliteClient(settings)
        adapters = build_adapters(client, settings)
        books = args.books.split(",") if args.books else list(adapters)
        leagues = args.leagues.split(",") if args.leagues else list(settings.enabled_leagues)
        for book in books:
            for league in leagues:
                if not adapters[book].supports(league):
                    continue
                try:
                    games = adapters[book].fetch(league)
                except FetchError as e:
                    print(f"{book:10} {league}: FAILED {type(e).__name__}: {e}")
                    if type(e).__name__ == "BlockedError":
                        print(f"{book:10} blocked; skipping its remaining leagues")
                        break
                    continue
                n_markets = sum(len(g.markets) for g in games)
                sample = ""
                if games:
                    g = games[0]
                    sample = f" e.g. {g.away_name} @ {g.home_name} " + ", ".join(
                        f"{m.market}:" + "/".join(f"{o.side}{'' if o.line is None else f' {o.line:+g}'} {o.price_american:+d}" for o in m.outcomes)
                        for m in g.markets
                    )
                print(f"{book:10} {league}: OK {len(games)} games, {n_markets} markets{sample}")
        return 0

    conn = connect(settings.db_path)
    init_db(conn)
    if args.cmd == "init-db":
        print(f"initialized {settings.db_path}")
    elif args.cmd == "create-key":
        raw = create_key(conn, args.name, args.tier, args.scopes.split(","), args.league)
        print("API key (shown once, store it now):")
        print(raw)
    elif args.cmd == "revoke-key":
        n = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE developer_name = ? AND revoked_at IS NULL",
            (iso(datetime.now(timezone.utc)), args.name),
        ).rowcount
        conn.commit()
        print(f"revoked {n} key(s)")
    elif args.cmd == "status":
        for r in conn.execute("SELECT * FROM poll_status ORDER BY job"):
            print(
                f"{r['job']:24} last_ok={r['last_success_at'] or '-':20} failures={r['consecutive_failures']} "
                f"next={r['next_allowed_at'] or '-'} count={r['last_count']} {r['last_error'] or ''}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
