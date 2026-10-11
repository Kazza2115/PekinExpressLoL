"""Palmarès : trophées, page et API, message Discord (auto et Admin), archive de l'édition."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import Challenge, ChallengeStatus, utcnow
from app.services import announce, notifications
from app.services.notifications import DiscordSendResult, build_palmares_message
from app.services.palmares import TROPHIES, award, award_trophies, edition_label
from tests.test_api import _admin_post
from tests.test_discord_embeds import make_settings
from tests.test_profile_api import _running_challenge_with_games
from tests.test_profile_stats import _member

SPECS = {spec.key: spec for spec in TROPHIES}


# --------------------------------------------------------------------------- #
# Trophées
# --------------------------------------------------------------------------- #


def test_edition_label():
    assert (edition_label(1), edition_label(2), edition_label(11)) == ("1re édition", "2e édition", "11e édition")


def test_ties_are_shared_and_minimums_apply():
    a = _member(1, lp_net=50, games=4, wins=3, losses=1, winrate=75.0, penta_kills=0, multikills=0)
    b = _member(2, lp_net=50, games=6, wins=4, losses=2, winrate=66.7, multikills=2, double_kills=2)
    c = _member(3, lp_net=-20, games=6, wins=1, losses=5, winrate=16.7, multikills=0)
    players = [a, b, c]
    assert [p.player_id for p, _v in award(SPECS["mvp"], players)] == [1, 2]  # ex aequo
    assert [p.player_id for p, _v in award(SPECS["winrate"], players)] == [2]  # A : moins de 5 parties
    assert [p.player_id for p, _v in award(SPECS["multikills"], players)] == [2]
    assert [p.player_id for p, _v in award(SPECS["red_lantern"], players)] == [3]
    assert award(SPECS["red_lantern"], [a]) == []  # seul joueur : pas de lanterne rouge
    assert award(SPECS["steals"], players) == []  # personne n'a volé d'objectif : non décerné
    same = [_member(i, games=10, wins=5, losses=5) for i in (1, 2, 3)]
    assert award(SPECS["grinder"], same) == []  # tous à égalité : rien ne les distingue
    assert [p.player_id for p, _v in award(SPECS["grinder"], same[:2])] == [1, 2]  # à deux : partagé


def test_trophies_payload_lists_every_trophy_with_labels():
    players = [_member(1, lp_net=80, kills=40, games=8, wins=6, losses=2), _member(2, lp_net=10, kills=12, games=8)]
    trophies = award_trophies(players, {})
    assert [t["key"] for t in trophies] == [spec.key for spec in TROPHIES]
    mvp = next(t for t in trophies if t["key"] == "mvp")
    assert mvp["winners"][0]["display_name"] == "J1" and mvp["winners"][0]["label"] == "+80 LP nets"
    kills = next(t for t in trophies if t["key"] == "kills")
    assert kills["winners"][0]["label"] == "40 kills"
    assert {t["category"] for t in trophies} == {"gloire", "honte"}


# --------------------------------------------------------------------------- #
# Page, API, Discord
# --------------------------------------------------------------------------- #


def _finished(client: TestClient, headers: dict) -> dict:
    _running_challenge_with_games(client, headers)
    _admin_post(client, headers, "/api/admin/challenge/finish")
    return client.get("/api/palmares").json()


def test_palmares_api_and_page_on_demo_data(client: TestClient, admin_headers: dict):
    assert client.get("/api/palmares").json()["current"] is None  # pas encore commencé
    data = _finished(client, admin_headers)
    current = data["current"]
    assert current["final"] is True and current["edition"] == 1 and current["edition_label"] == "1re édition"
    assert len(current["podium"]) == 3 and current["podium"][0]["position"] == 1
    assert [t["key"] for t in current["trophies"]] == [spec.key for spec in TROPHIES]
    players = current["players"]
    assert len(players) == 8
    assert [p["lp_net"] for p in players] == sorted((p["lp_net"] for p in players), reverse=True)
    # Les trophées de chaque joueur reprennent exactement les gagnants des trophées
    awarded = {(w["player_id"], t["key"]) for t in current["trophies"] for w in t["winners"]}
    assert awarded == {(p["player_id"], t["key"]) for p in players for t in p["trophies"]}
    assert all(len(p["best_wins"]) <= 3 and len(p["exploits"]) <= 9 for p in players)
    assert all(w["kills"] >= 0 and w["champion_name"] for p in players for w in p["best_wins"])

    page = client.get("/palmares")
    assert page.status_code == 200 and "palmares.js" in page.text and 'href="/palmares"' in page.text


def test_palmares_discord_message_fits_discord(client: TestClient, admin_headers: dict):
    current = _finished(client, admin_headers)["current"]
    content, embeds = build_palmares_message(current, settings=make_settings())
    winner = current["podium"][0]
    assert content.startswith("🏆 **Palmarès de la 1re édition** du ") and f"**{winner['name']}**" in content
    assert 2 <= len(embeds) <= 10
    assert sum(notifications._embed_size(e) for e in embeds) <= 6000  # noqa: SLF001
    assert all(e["url"].endswith("/palmares") for e in embeds)
    assert embeds[0]["description"].startswith("🥇 **")
    assert "Victoires et exploits de chacun" in embeds[-1]["description"]

    running = dict(current, final=False)
    content, _ = build_palmares_message(running, settings=make_settings())
    assert "Palmarès provisoire (1re édition, challenge en cours)" in content


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list]]:
    sent: list[tuple[str, list]] = []

    async def fake_post(content: str = "", *, embeds=None, **_: Any) -> DiscordSendResult:
        sent.append((content, list(embeds or [])))
        return DiscordSendResult(sent=True, status=204)

    monkeypatch.setattr(notifications, "post_discord", fake_post)
    return sent


def test_palmares_is_posted_once_30_min_after_the_end(client: TestClient, admin_headers: dict, session: Session,
                                                      monkeypatch: pytest.MonkeyPatch):
    sent = _capture(monkeypatch)
    _running_challenge_with_games(client, admin_headers)
    settings = make_settings()
    assert asyncio.run(announce.announce_palmares_if_due(settings=settings)) is False  # en cours
    _admin_post(client, admin_headers, "/api/admin/challenge/finish")
    end = session.exec(select(Challenge)).one().end_at
    assert asyncio.run(announce.announce_palmares_if_due(settings=settings, now=utcnow())) is False  # trop tôt
    later = utcnow() + timedelta(minutes=31)
    assert asyncio.run(announce.announce_palmares_if_due(settings=settings, now=later)) is True
    palmares = [content for content, _embeds in sent if content.startswith("🏆 **Palmarès")]
    assert palmares == [palmares[0]] and palmares[0].startswith("🏆 **Palmarès de la 1re édition**")
    assert asyncio.run(announce.announce_palmares_if_due(settings=settings, now=later)) is False  # une seule fois
    assert len([c for c, _e in sent if c.startswith("🏆 **Palmarès")]) == 1
    session.expire_all()
    challenge = session.exec(select(Challenge)).one()
    assert challenge.palmares_announced_at is not None and end is not None


def test_admin_can_post_the_palmares(client: TestClient, admin_headers: dict, monkeypatch: pytest.MonkeyPatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(announce, "get_settings", make_settings)
    assert client.post("/api/admin/announce-palmares").status_code == 401
    _running_challenge_with_games(client, admin_headers)
    r = _admin_post(client, admin_headers, "/api/admin/announce-palmares")
    assert r["sent"] is True and r["final"] is False and r["edition_label"] == "1re édition"
    assert "provisoire" in sent[-1][0]


def test_reset_archives_the_edition(client: TestClient, admin_headers: dict):
    first = _finished(client, admin_headers)["current"]
    r = _admin_post(client, admin_headers, "/api/admin/challenge/reset", {"keep_players": True})
    assert r["edition_archived"] == 1
    data = client.get("/api/palmares").json()
    assert data["current"] is None
    assert len(data["editions"]) == 1
    archived = data["editions"][0]
    assert archived["archived"] is True and archived["edition"] == 1
    assert archived["podium"][0]["name"] == first["podium"][0]["name"]
    # Nouvelle édition : numérotée 2, la 1re reste consultable
    _admin_post(client, admin_headers, "/api/admin/draw")
    _admin_post(client, admin_headers, "/api/admin/challenge/start")
    data = client.get("/api/palmares").json()
    assert data["current"]["edition"] == 2 and data["current"]["final"] is False
    assert [e["edition"] for e in data["editions"]] == [1]
    assert client.get("/api/state").json()["challenge"]["status"] == ChallengeStatus.RUNNING.value
