"""Double LP (« Aegis of Valor ») : repérage, retrait du bonus des LP nets, annonce Discord, Admin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlmodel import Session, select

from app.api.leaderboard import build_leaderboard
from app.db.models import Challenge, ChallengeStatus, MatchParticipant, Player, Queue, RankSnapshot
from app.events import bus
from app.riot.base import LeagueEntryDTO
from app.services import double_lp, gifs, notifications
from app.services import poller as poller_module
from app.services.double_lp import assess_double_lp, counted_lp, judge, lp_bonus, usual_gain
from app.services.notifications import (
    DoubleLpNotice,
    MatchNotice,
    build_double_lp_cancel_message,
    build_double_lp_message,
    match_embed,
)
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


def test_usual_gain_needs_three_reference_wins():
    assert usual_gain([]) is None
    assert usual_gain([22, 24]) is None
    assert usual_gain([22, 24, 23]) == 23
    assert usual_gain([20, 22, 23, 40]) == 22.5  # médiane : un gros gain isolé ne fausse pas tout


def test_judge_double_lp():
    assert judge(46, 23) is True  # le double exact
    assert judge(38, 23) is True  # ≥ 1,6 × le gain habituel
    assert judge(30, 23) is False  # bon gain, mais pas un double
    assert judge(28, 15) is False  # double d'un petit gain, sous le minimum de 30 LP
    assert judge(-20, 23) is None and judge(None, 23) is None  # défaites, LP inconnus
    # Sans gain habituel : jamais de double LP, même pour un très gros gain (après placements…)
    assert judge(50, None) is None


def test_bonus_is_half_the_gain_and_solo_only():
    win = MatchParticipant(match_id="M", player_id=1, game_start=NOW, game_duration=1500, champion_name="Ahri",
                           win=True, lp_change=47, double_lp=True)
    assert (lp_bonus(win), counted_lp(win)) == (23, 24)
    win.double_lp = False
    assert (lp_bonus(win), counted_lp(win)) == (0, 47)
    win.double_lp, win.lp_change = True, None
    assert (lp_bonus(win), counted_lp(win)) == (0, None)
    win.lp_change, win.queue = 47, Queue.FLEX
    assert lp_bonus(win) == 0  # la Flex ne compte pas pour les LP nets


def _player(session: Session) -> Player:
    player = Player(id=1, display_name="Mike", game_name="Mike", tag_line="EUW", puuid="p-mike")
    session.add(player)
    session.commit()
    return player


def _win(session: Session, match_id: str, lp: int | None, minutes: int, **fields: Any) -> MatchParticipant:
    start = utc(2026, 10, 10, 10, 0) + timedelta(minutes=minutes)
    row = MatchParticipant(match_id=match_id, player_id=1, queue=fields.pop("queue", Queue.SOLO), game_start=start,
                           game_duration=1500, champion_name="Ahri", win=True, lp_change=lp, **fields)
    session.add(row)
    session.commit()
    return row


def _verdicts(session: Session) -> dict[str, bool | None]:
    session.expire_all()
    return {row.match_id: row.double_lp for row in session.exec(select(MatchParticipant)).all()}


def test_assess_flags_a_doubled_win_and_respects_the_organizer(session: Session):
    player = _player(session)
    _win(session, "M1", 22, 0)
    _win(session, "M2", 24, 40)
    _win(session, "M3", 23, 80)
    _win(session, "M4", 46, 120)
    _win(session, "M5", 50, 160, double_lp=False, double_lp_manual=True)  # l'organisateur a tranché
    _win(session, "M6", None, 200)  # LP pas encore connus

    flagged = assess_double_lp(session, player)
    # Gain habituel : médiane de 22, 24, 23 et 50 (jugée normale par l'organisateur)
    assert [(g.match_id, usual) for g, usual in flagged] == [("M4", 23.5)]
    assert _verdicts(session) == {"M1": False, "M2": False, "M3": False, "M4": True, "M5": False, "M6": None}
    assert assess_double_lp(session, player) == []  # déjà jugées : rien de nouveau


def test_big_normal_wins_of_a_fresh_account_are_not_flagged(session: Session):
    """Juste après les placements, ou avec un MMR élevé, une victoire rapporte 40 LP ou plus."""
    player = _player(session)
    for index, lp in enumerate([45, 43, 41, 44, 42]):
        _win(session, f"M{index}", lp, index * 40)
    assert assess_double_lp(session, player) == []
    assert set(_verdicts(session).values()) == {False}


def test_first_wins_wait_for_a_reference(session: Session):
    player = _player(session)
    _win(session, "M1", 36, 0)  # 1re victoire : gain habituel inconnu, à revoir
    _win(session, "M2", 18, 40)
    assert assess_double_lp(session, player) == []
    _win(session, "M3", 17, 80)
    _win(session, "M4", 19, 120)
    # Gain habituel ≈ 18 : la 1re victoire (+36) était un double LP
    assert [(g.match_id, usual) for g, usual in assess_double_lp(session, player)] == [("M1", 18.0)]


def test_usual_gain_follows_the_climb(session: Session):
    """Le gain baisse pendant la montée : on compare aux victoires les plus proches dans le temps."""
    player = _player(session)
    for index, lp in enumerate([32, 31, 30, 30, 19, 18, 18, 17]):
        _win(session, f"M{index}", lp, index * 40)
    _win(session, "LATE", 36, 8 * 40)  # double de 18 ; la médiane de toute la journée (24,5) le raterait
    assert [g.match_id for g, _ in assess_double_lp(session, player)] == ["LATE"]


def test_win_mixed_with_an_unrecorded_game_is_not_judged(session: Session):
    """Relevés avant et après qui couvrent deux parties Riot (une partie d'avant le début non
    enregistrée) : les LP sont mélangés, la victoire n'est ni jugée ni prise comme référence."""
    player = _player(session)
    for captured, wins in ((utc(2026, 10, 10, 9, 0), 10), (utc(2026, 10, 10, 11, 0), 12)):
        session.add(RankSnapshot(player_id=1, queue=Queue.SOLO, tier="GOLD", rank="IV", lp=10, wins=wins, losses=8,
                                 absolute_lp=1210, captured_at=captured))
    session.commit()
    _win(session, "MERGED", 43, 0)  # fin à 10:25, entre les deux relevés : 21 + 22 LP
    for index, lp in enumerate([21, 22, 20]):
        _win(session, f"M{index}", lp, 120 + index * 40)
    assert assess_double_lp(session, player) == []
    assert _verdicts(session)["MERGED"] is None


def test_flex_wins_are_never_judged(session: Session):
    player = _player(session)
    for index, lp in enumerate([20, 21, 22]):
        _win(session, f"S{index}", lp, index * 40)
    _win(session, "FLEX", 44, 200, queue=Queue.FLEX)
    assert assess_double_lp(session, player) == []
    assert _verdicts(session)["FLEX"] is None


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
    stats = compute_player_stats(player=player, snapshots=snapshots, participants=participants + [doubled],
                                 window_start=WINDOW_START, window_end=None, games_limit=1, tz=PARIS, now=NOW)
    assert stats.lp_double_bonus == 0  # hors quota : ses LP sont déjà entièrement retirés


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


def test_double_lp_messages():
    win = discord_part(1, win=True)
    win.lp_change, win.double_lp = 48, True
    content, embeds = build_double_lp_message([DoubleLpNotice(mike(), None, win, usual_gain=23.5)],
                                              settings=make_settings())
    assert "Double LP repéré pour **Mike**" in content and "24 LP bonus" in content
    assert "+48 LP" in embeds[0]["description"] and "≈ 24 LP" in embeds[0]["description"]
    assert "**24 LP bonus sont retirés**" in embeds[0]["description"]

    other = discord_part(2, win=True, match_id="EUW1_2")
    other.lp_change, other.double_lp = 40, True
    content, embeds = build_double_lp_message(
        [DoubleLpNotice(mike(), None, win), DoubleLpNotice(mike(), None, other)], settings=make_settings()
    )
    assert content.startswith("⚡ 2 double LP repérés (Mike) : 44 LP bonus") and len(embeds) == 2

    content, _ = build_double_lp_message([DoubleLpNotice(mike(), None, win)], confirmed=True, settings=make_settings())
    assert "confirmé par l'organisateur" in content
    content, embeds = build_double_lp_cancel_message(mike(), None, win, 24, settings=make_settings())
    assert content == "↩️ Double LP annulé pour **Mike** : les 24 LP lui sont rendus"


def gold(rank: str, lp: int) -> list[LeagueEntryDTO]:
    return [LeagueEntryDTO(queue_type="RANKED_SOLO_5x5", tier="GOLD", rank=rank, league_points=lp, wins=10, losses=8)]


@pytest.fixture
def scripted(session: Session, monkeypatch: pytest.MonkeyPatch):
    """Poller sur une API scriptée, horloge pilotée par le test, Discord capturé."""
    t0 = datetime.now(timezone.utc) - timedelta(days=2)
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

    return {"cycle": cycle, "sent": sent, "clock": clock}


def _descriptions(message: tuple[str, list[dict[str, Any]] | None]) -> str:
    return " ".join(embed.get("description") or "" for embed in message[1] or [])


async def test_poller_detects_announces_and_removes_double_lp(session: Session, scripted):
    cycle, sent = scripted["cycle"], scripted["sent"]
    await cycle(0, gold("IV", 10))  # référence
    await cycle(40, gold("IV", 32), ("EUW1_1", 30))  # +22
    await cycle(80, gold("IV", 56), ("EUW1_2", 70))  # +24
    await cycle(120, gold("IV", 79), ("EUW1_3", 110))  # +23
    sent.clear()
    await cycle(160, gold("III", 25), ("EUW1_4", 150))  # +46 : double LP, connu dès le résultat
    assert len(sent) == 1 and "23 LP bonus retirés" in _descriptions(sent[0])
    assert any(e["data"]["lp_bonus"] == 23 for e in events_of("double_lp"))

    sent.clear()
    await cycle(195, gold("III", 25), ("EUW1_5", 190))  # résultat annoncé avant la mise à jour des LP
    assert len(sent) == 1 and "Double LP" not in _descriptions(sent[0])
    sent.clear()
    await cycle(200, gold("III", 73))  # +48 connus après coup : message à part
    assert len(sent) == 1 and sent[0][0].startswith("⚡ Double LP repéré pour **Mike**")
    await cycle(205, gold("III", 73))
    assert len(sent) == 1  # annoncé une seule fois

    session.expire_all()
    rows = {row.match_id: (row.lp_change, row.double_lp) for row in session.exec(select(MatchParticipant)).all()}
    assert rows == {"EUW1_1": (22, False), "EUW1_2": (24, False), "EUW1_3": (23, False), "EUW1_4": (46, True),
                    "EUW1_5": (48, True)}
    _teams, players = build_leaderboard(session, session.exec(select(Challenge)).one(), now=scripted["clock"]["now"])
    assert (players[0].lp_net, players[0].lp_double_bonus, players[0].double_lp_games) == (163 - 23 - 24, 47, 2)


async def test_double_lp_judged_the_next_day_is_still_announced(session: Session, scripted):
    """1re victoire à double LP le soir (pas encore de gain habituel), jugée le lendemain soir."""
    cycle, sent = scripted["cycle"], scripted["sent"]
    await cycle(0, gold("IV", 10))
    await cycle(40, gold("IV", 46), ("EUW1_1", 30))  # +36, à revoir
    day = 24 * 60
    await cycle(day + 40, gold("IV", 64), ("EUW1_2", day + 30))  # +18
    await cycle(day + 80, gold("IV", 81), ("EUW1_3", day + 70))  # +17
    sent.clear()
    await cycle(day + 120, gold("III", 0), ("EUW1_4", day + 110))  # +19 : 3 références, EUW1_1 jugée
    assert [m[0] for m in sent][-1] == "⚡ Double LP repéré pour **Mike** : 18 LP bonus retirés de ses LP nets"


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #


def test_admin_lists_and_corrects_double_lp(client, session: Session, admin_headers: dict,
                                           monkeypatch: pytest.MonkeyPatch):
    sent: list[str] = []

    async def fake_send(content: str, *, embeds=None, **_: Any) -> bool:
        sent.append(content)
        return True

    monkeypatch.setattr(notifications, "send_discord", fake_send)
    _player(session)
    _win(session, "M1", 22, 0, double_lp=False)
    _win(session, "M2", 24, 40, double_lp=False)
    _win(session, "M3", 23, 80, double_lp=False)
    doubled = _win(session, "M4", 46, 120, double_lp=True, double_lp_announced_at=utc(2026, 10, 10, 12, 30))
    missed = _win(session, "M5", 35, 160, double_lp=False)
    _win(session, "FLEX", 44, 200, queue=Queue.FLEX)

    data = client.get("/api/admin/double-lp", headers=admin_headers).json()
    assert (data["flagged"], data["total"]) == (1, 5)  # la Flex n'apparaît pas
    by_match = {item["match_id"]: item for item in data["items"]}
    # Gain habituel autour de M4 : médiane de 22, 24, 23 et 35
    assert (by_match["M4"]["double_lp"], by_match["M4"]["lp_bonus"], by_match["M4"]["usual_gain"]) == (True, 23, 23.5)

    # Annuler un double LP déjà annoncé : rectificatif sur Discord
    r = client.patch(f"/api/admin/double-lp/{doubled.id}", json={"double_lp": False}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["lp_bonus"] == 0 and r.json()["discord"] is True
    assert sent[-1] == "↩️ Double LP annulé pour **Mike** : les 23 LP lui sont rendus"
    # Confirmer un double LP raté : annoncé aussi
    r = client.patch(f"/api/admin/double-lp/{missed.id}", json={"double_lp": True}, headers=admin_headers)
    assert r.json()["lp_bonus"] == 17 and r.json()["discord"] is True
    assert sent[-1].startswith("⚡ Double LP confirmé par l'organisateur pour **Mike** : 17 LP")

    session.expire_all()
    rows = {row.match_id: row for row in session.exec(select(MatchParticipant)).all()}
    assert (rows["M4"].double_lp, rows["M4"].double_lp_manual, rows["M4"].double_lp_announced_at) == (False, True, None)
    assert rows["M5"].double_lp_announced_at is not None
    assert assess_double_lp(session, session.get(Player, 1)) == []  # plus jamais revues
    flex = rows["FLEX"]
    assert client.patch(f"/api/admin/double-lp/{flex.id}", json={"double_lp": True}, headers=admin_headers).status_code == 404
    assert client.patch("/api/admin/double-lp/99999", json={"double_lp": True}, headers=admin_headers).status_code == 404
    assert client.get("/api/admin/double-lp").status_code == 401
    assert double_lp.DOUBLE_RATIO == data["ratio"]
