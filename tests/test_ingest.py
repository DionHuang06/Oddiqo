from datetime import timedelta

from oddsapi.adapters import FanDuelAdapter
from oddsapi.ingest import ingest_book_games, resolve_team
from oddsapi.models import BookGame, BookMarket, Outcome

from .conftest import RECORDED_AT, load_all, load_fixture


def test_team_alias_resolution(loaded):
    assert resolve_team(loaded, "nba", "Golden State Warriors") == "t_nba_gs"
    assert resolve_team(loaded, "nba", "GS Warriors") == "t_nba_gs"
    assert resolve_team(loaded, "nba", "LA Clippers") == resolve_team(loaded, "nba", "Los Angeles Clippers")
    assert resolve_team(loaded, "nfl", "Kansas City Chiefs") == "t_nfl_kc"
    assert resolve_team(loaded, "nba", "Boston Celtics (COMMANDER)") is None


def test_all_three_books_attach_to_one_game(loaded):
    game_id = loaded.execute(
        "SELECT id FROM games WHERE home_team_id = 't_nba_gs' AND away_team_id = 't_nba_bos'"
    ).fetchone()["id"]
    books = {r["book_id"] for r in loaded.execute("SELECT DISTINCT book_id FROM prices WHERE game_id = ?", (game_id,))}
    assert books == {"fanduel", "pinnacle", "draftkings"}
    ids = {r["book_id"]: r["external_id"] for r in loaded.execute("SELECT * FROM game_odds_ids WHERE game_id = ?", (game_id,))}
    assert ids["fanduel"] == "36341635"


def test_match_rate_against_recorded_schedule(conn, settings):
    load_all(conn, settings, books=())
    a = FanDuelAdapter(None, settings)
    nfl = ingest_book_games(conn, "fanduel", a.source, a.parse("nfl", load_fixture("fanduel_nfl.json"), RECORDED_AT), 15)
    # The one miss is a Wednesday night game whose ESPN day was not recorded.
    assert nfl["matched"] == 17 and len(nfl["unmatched"]) == 1


def _game(spread_home_line, spread_home_price):
    return BookGame(
        external_id="x1", league="nba", starts_at=RECORDED_AT + timedelta(minutes=23),
        home_name="Golden State Warriors", away_name="Boston Celtics", captured_at=RECORDED_AT,
        markets=[BookMarket("spread", [Outcome("home", spread_home_price, spread_home_line), Outcome("away", -110, -spread_home_line)])],
    )


def test_snapshots_only_on_change(loaded):
    first = ingest_book_games(loaded, "fanduel", "test", [_game(8.5, -110)], 15)
    same = ingest_book_games(loaded, "fanduel", "test", [_game(8.5, -110)], 15)
    price_move = ingest_book_games(loaded, "fanduel", "test", [_game(8.5, -115)], 15)
    line_move = ingest_book_games(loaded, "fanduel", "test", [_game(9.0, -115)], 15)
    assert (first["snapshots"], same["snapshots"], price_move["snapshots"], line_move["snapshots"]) == (2, 0, 1, 2)
    rows = loaded.execute(
        "SELECT side, line, price_american FROM prices p JOIN games g ON g.id = p.game_id "
        "WHERE p.book_id = 'fanduel' AND market = 'spread' AND g.home_team_id = 't_nba_gs' ORDER BY line"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("t_nba_bos", -9.0, -110), ("t_nba_gs", 9.0, -115)]


def test_no_match_outside_time_tolerance(loaded):
    g = _game(8.5, -110)
    g.external_id = "x2"
    g.starts_at = RECORDED_AT + timedelta(hours=3)
    assert ingest_book_games(loaded, "fanduel", "test", [g], 15)["matched"] == 0
