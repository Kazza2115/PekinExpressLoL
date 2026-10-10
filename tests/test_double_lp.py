"""Double LP (« Aegis of Valor ») : repérage, retrait du bonus des LP nets, annonce Discord, Admin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlmodel import Session, select

from app.api.leaderboard import build_leaderboard
from app.db.models import Challenge, ChallengeStatus, MatchParticipant, Player, Queue
from app.events import bus
from app.riot.base import LeagueEntryDTO
from app.services import double_lp, gifs
from app.services import poller as poller_module
from app.services.double_lp import assess_double_lp, counted_lp, judge, lp_bonus, usual_gain
from app.services.notifications import MatchNotice, build_double_lp_message, match_embed
from app.services.poller import Poller
from app.services.stats import compute_player_stats
from app.state import state
from tests.test_discord_embeds import make_settings, mike
from tests.test_discord_embeds import part as discord_part
from tests.test_poller import ScriptedAPI, events_of, make_challenge, make_player, match_json
from tests.test_profile_stats import NOW, PARIS, WINDOW_START, game, scenario, utc

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --------------------------------------------------------------------------- #
# Règle de repérage
# --------------------------------------------------------------------------- #


def test_usual_gain_needs_two_reference_wins():
    assert usual_gain([]) is None
    assert usual_gain([22]) is None
    assert usual_gain([22, 24]) == 23
    assert usual_gain([20, 22, 40]) == 22  # médiane : un gros gain isolé ne fausse pas tout


def test_judge_double_lp():
    assert judge(46, 23) is True  # le double exact
    assert judge(38, 23) is True  # ≥ 1,6 × le gain habituel
    assert judge(30, 23) is False  # bon gain, mais pas un double
    assert judge(28, 15) is False  # double d'un petit gain, sous le minimum de 30 LP
    assert judge(-20, 23) is None and judge(None, 23) is None  # défaites, LP inconnus
    # Sans gain habituel : seul un gain énorme est retenu, le reste attend d'autres victoires
    assert judge(44, None) is True
    assert judge(32, None) is None


def test_bonus_is_half_the_gain():
    win = MatchParticipant(match_id="M", player_id=1, game_start=NOW, game_duration=1500, champion_name="Ahri",
                           win=True, lp_change=47, double_lp=True)
    assert (lp_bonus(win), counted_lp(win)) == (23, 24)
    win.double_lp = False
    assert (lp_bonus(win), counted_lp(win)) == (0, 47)
    win.double_lp, win.lp_change = True, None
    assert (lp_bonus(win), counted_lp(win)) == (0, None)


def _win(session: Session, match_id: str, lp: int | None, minutes: int, **fields: Any) -> MatchParticipant:
    start = utc(2026, 10, 10, 10, 0) + timedelta(minutes=minutes)
    row = MatchParticipant(match_id=match_id, player_id=1, queue=Queue.SOLO, game_start=start, game_duration=1500,
                           champion_name="Ahri", win=True, lp_change=lp, **fields)
    session.add(row)
    session.commit()
    return row


def test_assess_flags_a_doubled_win_and_respects_the_organizer(session: Session):
    player = Player(id=1, display_name="Mike", game_name="Mike", tag_line="EUW", puuid="p-mike")
    session.add(player)
    session.commit()
    _win(session, "M1", 22, 0)
    _win(session, "M2", 24, 40)
    doubled = _win(session, "M3", 46, 80)
    _win(session, "M4", 50, 120, double_lp=False, double_lp_manual=True)  # l'organisateur a tranché
    _win(session, "M5", None, 160)  # LP pas encore connus

    flagged = assess_double_lp(session, player)
    # Gain habituel : médiane de 22, 24 et 50 (jugée normale par l'organisateur)
    assert [(game.match_id, usual) for game, usual in flagged] == [("M3", 24.0)]
    session.expire_all()
    rows = {row.match_id: row.double_lp for row in session.exec(select(MatchParticipant)).all()}
    assert rows == {"M1": False, "M2": False, "M3": True, "M4": False, "M5": None}
    assert assess_double_lp(session, player) == []  # déjà jugées : rien de nouveau
    assert doubled.id is not None


def test_first_wins_wait_for_a_reference_unless_huge(session: Session):
    player = Player(id=1, display_name="Mike", game_name="Mike", tag_line="EUW", puuid="p-mike")
    session.add(player)
    session.commit()
    _win(session, "M1", 30, 0)  # 1re victoire, gain inconnu : à revoir
    _win(session, "M2", 48, 40)  # énorme : repérée tout de suite
    assert [game.match_id for game, _ in assess_double_lp(session, player)] == ["M2"]
    _win(session, "M3", 16, 80)
    _win(session, "M4", 17, 120)
    # Gain habituel ≈ 17 : la 1re victoire (+30) était un double LP
    assert [game.match_id for game, _ in assess_double_lp(session, player)] == ["M1"]


# --------------------------------------------------------------------------- #
# LP nets
# --------------------------------------------------------------------------- #


def test_bonus_is_removed_from_net_lp():
    player, snapshots, participants = scenario()
    base = compute_player_stats(player=player, snapshots=snapshots, participants=participants,
                                window_start=WINDOW_START, window_end=None, games_limit=10, tz=PARIS, now=NOW)
    doubled = next(p for p in participants if p.lp_change == 25)
    doubled.double_lp = True
    stats = compute_player_stats(player=player, snapshots=snapshots, participants=participants,
                                 window_start=WINDOW_START, window_end=None, games_limit=10, tz=PARIS, now=NOW)
    assert (stats.double_lp_games, stats.lp_double_bonus) == (1, 12)
    assert stats.lp_net == base.lp_net - 12
    assert stats.best_lp_gain == 18  # la victoire doublée ne compte plus que pour 13 LP


def test_bonus_of_an_over_quota_game_is_not_removed_twice():
    player, snapshots, participants = scenario()
    # 23:00 à Paris le 10 : 4e partie de la journée, hors quota avec une limite d'une partie
    doubled = game(utc(2026, 10, 10, 21, 0), 1500, win=True, lp_change=40, double_lp=True)
    base = compute_player_stats(player=player, snapshots=snapshots, participants=participants,
                                window_start=WINDOW_START, window_end=None, games_limit=1, tz=PARIS, now=NOW)
    stats = compute_player_stats(player=player, snapshots=snapshots, participants=participants + [doubled],
                                 window_start=WINDOW_START, window_end=None, games_limit=1, tz=PARIS, now=NOW)
    assert stats.lp_double_bonus == 0  # hors quota : ses LP sont déjà entièrement retirés
    assert base.lp_double_bonus == 0


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #


def test_match_card_mentions_the_double_lp():
    win = discord_part(1, win=True)
    win.lp_change, win.double_lp = 46, True
    embed = match_embed(MatchNotice(player=mike(), team=None, participant=win, lp_change=46), settings=make_settings())
    assert "Double LP" in embed["description"] and "23 LP bonus retirés" in embed["description"]
    over = match_embed(MatchNotice(player=mike(), team=None, participant=win, lp_change=46, over_quota=True),
                       settings=make_settings())
    assert "Double LP" not in over["description"]  # hors quota : rien n'est retiré


def test_late_double_lp_message():
    win = discord_part(1, win=True)
    win.lp_change, win.double_lp = 48, True
    content, embeds = build_double_lp_message(mike(), None, win, usual_gain=23.5, settings=make_settings())
    assert "Double LP repéré pour **Mike**" in content and "24 LP bonus" in content
    assert "+48 LP" in embeds[0]["description"] and "≈ 24 LP" in embeds[0]["description"]
    assert "**24 LP bonus sont retirés**" in embeds[0]["description"]


def gold(rank: str, lp: int) -> list[LeagueEntryDTO]:
    return [LeagueEntryDTO(queue_type="RANKED_SOLO_5x5", tier="GOLD", rank=rank, league_points=lp, wins=10, losses=8)]


async def test_poller_detects_announces_and_removes_double_lp(session: Session, monkeypatch: pytest.MonkeyPatch):
    t0 = datetime.now(timezone.utc) - timedelta(hours=4)
    clock = {"now": t0}
    monkeypatch.setattr(poller_module, "_utcnow", lambda: clock["now"])
    make_challenge(session, ChallengeStatus.RUNNING, start_at=t0 - timedelta(hours=1))
    make_player(session, "Mike", "p-mike")

    sent: list[tuple[str, list[dict[str, Any]] | None]] = []

    async def fake_send(content: str, *, embeds=None, settings=None, **_: Any) -> bool:
        sent.append((content, embeds))
        return True

    async def no_gif(win: bool, **_: Any) -> None:
        return None

    monkeypatch.setattr("app.services.poller.send_discord", fake_send)
    monkeypatch.setattr(gifs, "find_gif", no_gif)

    api = ScriptedAPI()
    poller = Poller(api, bus, state, settings=make_settings())

    async def cycle(at_minutes: int, entries: list[LeagueEntryDTO], new_match: tuple[str, int] | None = None) -> None:
        if new_match is not None:
            match_id, ended = new_match
            start = t0 + timedelta(minutes=ended) - timedelta(seconds=1500)
            api.match_ids["p-mike"] = [match_id, *api.match_ids.get("p-mike", [])]
            api.matches[match_id] = match_json(match_id, [("p-mike", "Ahri", True)], start, 1500)
        api.entries["p-mike"] = entries
        clock["now"] = t0 + timedelta(minutes=at_minutes)
        report = await poller.poll_once()
        assert report.errors == []

    await cycle(0, gold("IV", 10))  # référence
    await cycle(40, gold("IV", 32), ("EUW1_1", 30))  # +22
    await cycle(80, gold("IV", 56), ("EUW1_2", 70))  # +24
    sent.clear()
    await cycle(120, gold("III", 2), ("EUW1_3", 110))  # +46 : double LP, connu dès le résultat
    assert len(sent) == 1
    assert "Double LP" in sent[0][1][0]["description"] and "23 LP bonus retirés" in sent[0][1][0]["description"]
    assert any(e["data"]["lp_bonus"] == 23 for e in events_of("double_lp"))

    sent.clear()
    await cycle(155, gold("III", 2), ("EUW1_4", 150))  # résultat annoncé avant que Riot ne mette les LP à jour
    assert len(sent) == 1 and "Double LP" not in (sent[0][1][0].get("description") or "")
    sent.clear()
    await cycle(160, gold("III", 50))  # +48 connus après coup : message à part
    assert len(sent) == 1 and sent[0][0].startswith("⚡ Double LP repéré pour **Mike**")
    await cycle(165, gold("III", 50))
    assert len(sent) == 1  # annoncé une seule fois

    session.expire_all()
    rows = {row.match_id: (row.lp_change, row.double_lp) for row in session.exec(select(MatchParticipant)).all()}
    assert rows == {"EUW1_1": (22, False), "EUW1_2": (24, False), "EUW1_3": (46, True), "EUW1_4": (48, True)}
    _teams, players = build_leaderboard(session, session.exec(select(Challenge)).one(), now=clock["now"])
    assert (players[0].lp_net, players[0].lp_double_bonus, players[0].double_lp_games) == (140 - 23 - 24, 47, 2)


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #


def test_admin_lists_and_corrects_double_lp(client, session: Session, admin_headers: dict):
    session.add(Player(id=1, display_name="Mike", game_name="Mike", tag_line="EUW", puuid="p-mike"))
    session.commit()
    _win(session, "M1", 22, 0, double_lp=False)
    _win(session, "M2", 24, 40, double_lp=False)
    doubled = _win(session, "M3", 46, 80, double_lp=True)
    headers = admin_headers

    data = client.get("/api/admin/double-lp", headers=headers).json()
    assert (data["flagged"], data["total"]) == (1, 3)
    top = data["items"][0]
    assert (top["match_id"], top["double_lp"], top["lp_bonus"], top["usual_gain"], top["manual"]) == ("M3", True, 23, 23.0, False)

    r = client.patch(f"/api/admin/double-lp/{doubled.id}", json={"double_lp": False}, headers=headers)
    assert r.status_code == 200 and r.json()["lp_bonus"] == 0
    session.expire_all()
    row = session.get(MatchParticipant, doubled.id)
    assert (row.double_lp, row.double_lp_manual) == (False, True)
    assert assess_double_lp(session, session.get(Player, 1)) == []  # plus jamais revue
    assert client.patch("/api/admin/double-lp/99999", json={"double_lp": True}, headers=headers).status_code == 404
    assert client.get("/api/admin/double-lp").status_code == 401
    assert double_lp.DOUBLE_RATIO == data["ratio"]
