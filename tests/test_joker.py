"""Joker (+3 parties comptées le jour où le duo l'active) et LP des parties hors des heures du challenge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api import routes_api
from app.db.models import Challenge, ChallengeStatus, Joker, Match, MatchParticipant, Queue, RankSnapshot
from app.services.notifications import format_joker_used, format_match_recorded
from app.services.stats import compute_player_stats, excluded_lp, split_daily_quota
from tests.conftest import ADMIN_PASSWORD
from tests.test_poller import events_of
from tests.test_stats import PARIS, game, make_player as stats_player, snap, utc

H = {"X-Admin-Password": ADMIN_PASSWORD}
START = utc(2026, 10, 10, 7, 0)  # samedi 9h Paris
END = utc(2026, 10, 11, 22, 0)  # lundi 00h Paris


# --------------------------------------------------------------------------- #
# Quota avec joker
# --------------------------------------------------------------------------- #


def day_games(count: int, first_end: datetime) -> list:
    return [game(first_end - timedelta(minutes=30) + timedelta(hours=i), win=True) for i in range(count)]


def test_joker_adds_three_counted_games_after_activation() -> None:
    games = day_games(14, utc(2026, 10, 10, 8, 0))  # 14 parties samedi, fin 8h, 9h… (UTC)
    # Sans joker : 10 comptées
    counted, over = split_daily_quota(games, PARIS, 10)
    assert (len(counted), len(over)) == (10, 4)
    # Joker activé avant la 11e : 13 comptées
    activated = games[9].game_start + timedelta(minutes=31)  # juste après la fin de la 10e
    counted, over = split_daily_quota(games, PARIS, 10, {"2026-10-10": (activated, 3)})
    assert (len(counted), len(over)) == (13, 1) and over == [games[13]]


def test_joker_cannot_rescue_games_already_played() -> None:
    games = day_games(13, utc(2026, 10, 10, 8, 0))
    after_all = games[-1].game_start + timedelta(hours=2)  # activé après les 13 parties
    counted, over = split_daily_quota(games, PARIS, 10, {"2026-10-10": (after_all, 3)})
    assert (len(counted), len(over)) == (10, 3)
    # Activé entre la 11e et la 12e : seules la 12e et la 13e profitent du joker
    between = games[10].game_start + timedelta(minutes=40)
    counted, over = split_daily_quota(games, PARIS, 10, {"2026-10-10": (between, 3)})
    assert over == [games[10]] and len(counted) == 12


def test_joker_only_on_its_day() -> None:
    games = day_games(12, utc(2026, 10, 11, 7, 0))  # dimanche
    counted, _ = split_daily_quota(games, PARIS, 10, {"2026-10-10": (utc(2026, 10, 10, 8, 0), 3)})
    assert len(counted) == 10


# --------------------------------------------------------------------------- #
# LP des parties jouées hors des heures (bords de la fenêtre)
# --------------------------------------------------------------------------- #


def counter_snap(at: datetime, lp: int, wins: int, losses: int) -> RankSnapshot:
    return snap(at, "MASTER", None, lp, wins=wins, losses=losses)


def stats_for(snapshots, games, **kwargs):
    return compute_player_stats(
        player=stats_player(), snapshots=snapshots, participants=games, window_start=START, window_end=END,
        games_limit=10, tz=PARIS, now=END + timedelta(hours=1), **kwargs,
    )


def test_game_ending_after_the_end_does_not_count_its_lp() -> None:
    """Dimanche 23h38 : +20 ; lundi 00h03 : −22, relevé à 00h05 (dans la grâce de 10 min)."""
    g1 = game(END - timedelta(minutes=52), win=True, duration=1800)  # fin 23h38 Paris
    g2 = game(END - timedelta(minutes=27), win=False, duration=1800)  # fin 00h03 Paris
    snaps = [
        counter_snap(START - timedelta(hours=1), 50, 100, 100),
        counter_snap(END - timedelta(minutes=20), 70, 101, 100),
        counter_snap(END + timedelta(minutes=5), 48, 101, 101),
    ]
    # g2 enregistré ou non (« Terminer » cliqué avant) : même résultat
    for games in ([g1, g2], [g1]):
        s = stats_for(snaps, games)
        assert s.games == 1 and s.lp_net == 20 and s.lp_outside_window == -22


def test_game_ending_before_the_start_does_not_count_its_lp() -> None:
    """Samedi 8h58 : −22, relevé à 9h00m40 (après le début) : ne compte pas."""
    snaps = [
        counter_snap(START - timedelta(hours=10), 50, 100, 100),
        counter_snap(START + timedelta(seconds=40), 28, 100, 101),
        counter_snap(START + timedelta(hours=1), 48, 101, 101),
    ]
    g_in = game(START + timedelta(minutes=20), win=True, duration=1800)  # fin 9h50
    s = stats_for(snaps, [g_in])
    assert s.lp_net == 20 and s.lp_outside_window == -22 and s.games == 1


def test_overnight_games_seen_late_are_removed_pro_rata() -> None:
    """Site éteint la nuit : la référence est vieille ; le 1er relevé (10h) couvre 2 parties de la
    nuit (−40) et 1 partie du challenge (+20) : la part des 2 parties de nuit est retirée."""
    snaps = [
        counter_snap(START - timedelta(hours=14), 50, 100, 100),
        counter_snap(START + timedelta(hours=1), 30, 101, 102),  # 3 parties : −20 au total
    ]
    g_in = game(START + timedelta(minutes=10), win=True, duration=1800)
    s = stats_for(snaps, [g_in])
    assert s.lp_over_quota_approx is True
    assert s.lp_net == -20 - round(-20 * 2 / 3)


def test_excluded_lp_without_counters_changes_nothing() -> None:
    snaps = [snap(START - timedelta(hours=1), "MASTER", None, 50), snap(START + timedelta(hours=1), "MASTER", None, 70)]
    snaps[0].wins = snaps[0].losses = None  # type: ignore[assignment]
    assert excluded_lp(snaps, [], [], start=START, end=END) == (0, 0, False)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def start_duo(client: TestClient, monkeypatch: pytest.MonkeyPatch, now: datetime) -> tuple[int, list[int]]:
    ids = []
    for name, riot in (("Mike", "Mike Demo#EUW"), ("Léa", "Lea Demo#EUW"), ("Tom", "Tom Demo#EUW")):
        r = client.post("/api/players", json={"display_name": name, "riot_id": riot})
        assert r.status_code == 201, r.text
        ids.append(r.json()["player"]["id"])
    team = client.post("/api/admin/teams", headers=H, json={"player_ids": ids[:2]}).json()["team"]
    client.post("/api/admin/teams", headers=H, json={"player_ids": ids[2:]})
    r = client.post("/api/admin/challenge/start", headers=H, json={"start_at": START.isoformat(), "end_at": END.isoformat()})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(routes_api, "utcnow", lambda: now)
    return team["id"], ids


def test_joker_route_rules(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    team_id, ids = start_duo(client, monkeypatch, START - timedelta(hours=1))
    url = f"/api/teams/{team_id}/joker"
    # Avant le début : refusé
    r = client.post(url, json={"player_id": ids[0]})
    assert r.status_code == 400 and "pas encore commencé" in r.json()["detail"]

    monkeypatch.setattr(routes_api, "utcnow", lambda: START + timedelta(hours=3))
    # Joueur d'un autre duo : refusé
    assert client.post(url, json={"player_id": ids[2]}).status_code == 400
    r = client.post(url, json={"player_id": ids[1]})
    assert r.status_code == 201, r.text
    joker = r.json()["joker"]
    assert joker["day"] == "2026-10-10" and joker["limit"] == 13 and joker["display_name"] == "Léa"
    assert events_of("joker_used")
    # Une seule fois
    assert "déjà actif aujourd'hui" in client.post(url, json={"player_id": ids[0]}).json()["detail"]
    monkeypatch.setattr(routes_api, "utcnow", lambda: START + timedelta(days=1))
    assert "déjà utilisé son joker" in client.post(url, json={"player_id": ids[0]}).json()["detail"]

    duo = next(t for t in client.get("/api/duos").json()["teams"] if t["team_id"] == team_id)
    assert duo["jokers_used"] == 1 and duo["jokers_left"] == 0 and duo["jokers"][0]["player_name"] == "Léa"

    # L'Admin l'annule : le duo le récupère
    assert client.delete(f"/api/admin/jokers/{joker['joker_id']}").status_code in (401, 403)
    assert client.delete(f"/api/admin/jokers/{joker['joker_id']}", headers=H).status_code == 200
    assert client.post(url, json={"player_id": ids[0]}).status_code == 201

    # Après la fin : refusé
    monkeypatch.setattr(routes_api, "utcnow", lambda: END + timedelta(minutes=1))
    other = next(t for t in client.get("/api/duos").json()["teams"] if t["team_id"] != team_id)
    assert "fini" in client.post(f"/api/teams/{other['team_id']}/joker", json={"player_id": ids[2]}).json()["detail"]


def test_joker_counts_in_player_stats(client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team_id, ids = start_duo(client, monkeypatch, START + timedelta(hours=1))
    assert client.post(f"/api/teams/{team_id}/joker", json={"player_id": ids[0]}).status_code == 201
    for i in range(14):
        end = START + timedelta(hours=1, minutes=40 * (i + 1))
        mid = f"EUW1_J{i}"
        session.add(Match(match_id=mid, queue_id=420, game_start=end - timedelta(minutes=30), game_end=end, game_duration=1800, raw_json="{}"))
        session.add(MatchParticipant(match_id=mid, player_id=ids[0], queue=Queue.SOLO, game_start=end - timedelta(minutes=30),
                                     game_end=end, game_duration=1800, champion_name="Ahri", win=True))
    session.commit()
    stats = client.get(f"/api/players/{ids[0]}").json()["stats"]
    assert stats["games"] == 13 and stats["games_over_quota"] == 1
    assert stats["joker_days"] == ["2026-10-10"]
    by_day = {d["day"]: d for d in stats["by_day"]}
    assert by_day["2026-10-10"]["limit"] == 13 and by_day["2026-10-10"]["joker"] is True
    # La coéquipière profite aussi du joker du duo
    assert client.get(f"/api/players/{ids[1]}").json()["stats"]["joker_days"] == ["2026-10-10"]


def test_dates_sent_with_start_and_reopen_after_finish(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import routes_admin

    start_duo(client, monkeypatch, START + timedelta(hours=1))
    # Démarrer une seconde fois : refusé, dates intactes
    r = client.post("/api/admin/challenge/start", headers=H, json={"start_at": None, "end_at": None})
    assert r.status_code == 400
    challenge = client.get("/api/state").json()["challenge"]
    assert challenge["start_at"] and challenge["end_at"]
    # Effacer le début d'un challenge en cours : refusé
    assert client.patch("/api/admin/challenge", headers=H, json={"start_at": None}).status_code == 400
    # Fini par erreur (fin dimanche 00h saisie), puis fin repoussée : le challenge reprend
    monkeypatch.setattr(routes_admin, "utcnow", lambda: START + timedelta(hours=2))
    client.post("/api/admin/challenge/finish", headers=H)
    r = client.patch("/api/admin/challenge", headers=H, json={"end_at": "2099-10-12T00:00"})
    assert r.json()["reopened"] is True and r.json()["challenge"]["status"] == "running"
    assert r.json()["challenge"]["start_at"] == challenge["start_at"]


def test_joker_settings_editable(client: TestClient) -> None:
    r = client.patch("/api/admin/challenge", headers=H, json={"jokers_per_team": 2, "joker_extra_games": 4})
    assert r.status_code == 200
    assert (r.json()["challenge"]["jokers_per_team"], r.json()["challenge"]["joker_extra_games"]) == (2, 4)


def test_messages() -> None:
    assert format_joker_used("Duo Rouge", "Mike", 3, 10).startswith("🃏 **Duo Rouge** active son joker (par Mike) : **13 parties**")
    participant = game(END + timedelta(minutes=1), win=True)
    text = format_match_recorded(stats_player(), None, participant, 20, outside_window=True)
    assert "hors des heures du challenge" in text
