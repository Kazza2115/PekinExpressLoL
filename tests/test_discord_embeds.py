"""Messages Discord enrichis : cartes de stats, mention du rôle, GIF Klipy, duo dans la même partie."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from sqlmodel import Session, select

from app import config
from app.config import Settings
from app.db.models import Challenge, ChallengeStatus, MatchParticipant, Player, Queue, RankSnapshot, Team
from app.events import bus
from app.riot.base import ActiveGameDTO
from app.services import gifs, notifications, portal
from app.services.notifications import MatchNotice, build_match_message, group_match_notices, match_embed
from app.services.poller import Poller
from app.services.scoreboard import player_highlights, team_luck
from app.state import state
from tests.test_poller import ScriptedAPI, gold_iv, make_player, match_json, utcnow
from tests.test_scoreboard import match_of, participant as sb_participant

pytestmark = pytest.mark.anyio

WEBHOOK_URL = "https://discord.test/api/webhooks/123/abc"
ROLE_ID = "123456789012345678"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_gif_cache():
    gifs.reset_cache()
    yield
    gifs.reset_cache()


def make_settings(**overrides: Any) -> Settings:
    settings = Settings(
        riot_api_key="",
        riot_platform="euw1",
        riot_region="europe",
        demo_mode=True,
        poll_interval_seconds=10,
        track_flex=False,
        admin_password="x",
        database_url="sqlite://",
        games_per_day=10,
        max_players=8,
        timezone="Europe/Paris",
        discord_webhook_url=WEBHOOK_URL,
        base_url="http://localhost:8000",
    )
    return replace(settings, **overrides)


def recorder(seen: list[httpx.Request], status_code: int = 204, body: Any = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json=body if body is not None else {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def mike(team_id: int | None = 1) -> Player:
    return Player(id=1, display_name="Mike", game_name="EL PSYY", tag_line="EUW", puuid="p-mike", profile_icon_id=29, team_id=team_id)


def lea(team_id: int | None = 1) -> Player:
    return Player(id=2, display_name="Léa", game_name="Lea", tag_line="EUW", puuid="p-lea", team_id=team_id)


def duo_rouge() -> Team:
    return Team(id=1, name="Duo Rouge", color="#ef4444", slot=1)


def part(player_id: int, *, win: bool = True, match_id: str = "EUW1_8008590279", side: int = 100, champion: str = "MonkeyKing") -> MatchParticipant:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    return MatchParticipant(
        match_id=match_id,
        player_id=player_id,
        queue=Queue.SOLO,
        game_start=now - timedelta(seconds=1947),
        game_end=now,
        game_duration=1947,
        champion_name=champion,
        team_side=side,
        win=win,
        kills=15,
        deaths=4,
        assists=15,
        cs=234,
        damage_to_champions=35512,
        vision_score=47,
        kill_participation=68.2,
    )


HIGHLIGHTS = {
    "duration_s": 1947,
    "score": 9.75,
    "badge": "MVP",
    "place": 1,
    "players_count": 10,
    "pings": 53,
    "team_luck": "Très bonne",
    "gold_diff_lane": 1250,
    "largest_multi_kill": 5,
}


# --------------------------------------------------------------------------- #
# Envoi : mention du rôle et cartes
# --------------------------------------------------------------------------- #


async def test_send_discord_mentions_the_role_and_sends_embeds():
    seen: list[httpx.Request] = []
    async with recorder(seen) as client:
        sent = await notifications.send_discord(
            "Bonjour", embeds=[{"title": "Carte"}], settings=make_settings(discord_role_id=ROLE_ID), client=client
        )
    assert sent is True
    payload = json.loads(seen[0].content)
    assert payload["content"] == f"<@&{ROLE_ID}> Bonjour"
    assert payload["allowed_mentions"] == {"parse": [], "roles": [ROLE_ID]}
    assert payload["embeds"] == [{"title": "Carte"}]


async def test_send_discord_without_role_mentions_nobody():
    seen: list[httpx.Request] = []
    async with recorder(seen) as client:
        await notifications.send_discord("Bonjour", settings=make_settings(), client=client)
    payload = json.loads(seen[0].content)
    assert payload == {"content": "Bonjour", "allowed_mentions": {"parse": []}}


async def test_send_discord_mention_can_be_disabled_and_long_content_keeps_the_mention():
    seen: list[httpx.Request] = []
    settings = make_settings(discord_role_id=ROLE_ID)
    async with recorder(seen) as client:
        await notifications.send_discord("x", mention=False, settings=settings, client=client)
        await notifications.send_discord("a" * 5000, settings=settings, client=client)
    first, second = (json.loads(r.content) for r in seen)
    assert first["content"] == "x" and first["allowed_mentions"] == {"parse": []}
    assert second["content"].startswith(f"<@&{ROLE_ID}> a")
    assert len(second["content"]) <= notifications.DISCORD_MAX_LENGTH


def test_clamp_embed_respects_discord_limits():
    embed = notifications.clamp_embed(
        {"title": "t" * 400, "fields": [{"name": "", "value": "v" * 2000}] * 30, "footer": {"text": "f" * 3000}}
    )
    assert len(embed["title"]) == 256
    assert len(embed["fields"]) == 25
    assert embed["fields"][0]["name"] == "​"
    assert len(embed["fields"][0]["value"]) == 1024
    assert len(embed["footer"]["text"]) == 2048


def test_role_id_parsing_accepts_raw_id_or_mention():
    assert config.parse_role_id(ROLE_ID) == ROLE_ID
    assert config.parse_role_id(f"<@&{ROLE_ID}>") == ROLE_ID
    assert config.parse_role_id("@PekinExpress") == ""


def test_gif_search_terms_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_GIF_SEARCH_WIN", "Chad ; Goofy Drake,  Happy cat")
    assert config._env_terms("DISCORD_GIF_SEARCH_WIN", ()) == ("Chad", "Goofy Drake", "Happy cat")
    monkeypatch.setenv("DISCORD_GIF_SEARCH_WIN", "off")
    assert config._env_terms("DISCORD_GIF_SEARCH_WIN", ("x",)) == ()
    monkeypatch.delenv("DISCORD_GIF_SEARCH_WIN")
    assert config._env_terms("DISCORD_GIF_SEARCH_WIN", ("x",)) == ("x",)
    monkeypatch.setenv("DISCORD_GIF_LOSS", "https://giphy.com/gifs/sad-loser-xBNQZSCmjMxvDenhyz https://media.tenor.com/a/b.gif")
    assert config._env_gifs("DISCORD_GIF_LOSS") == (
        "https://media.giphy.com/media/xBNQZSCmjMxvDenhyz/giphy.gif",
        "https://media.tenor.com/a/b.gif",
    )


def test_default_gif_categories():
    settings = make_settings()
    assert settings.gif_search_loss == (
        "Monkey", "Goofy Dog", "Charlie Kirk", "Goofy Patrick", "Spongebob cursed", "Indian Goofy",
    )
    assert settings.gif_search_win == (
        "Chad", "Lightskin", "Extreme lightskin", "Handsome spongebob", "Happy Netanyahu", "Goofy Drake",
    )


# --------------------------------------------------------------------------- #
# Carte de résultat
# --------------------------------------------------------------------------- #


def test_match_embed_looks_like_a_tracker_card():
    notice = MatchNotice(
        player=mike(), team=duo_rouge(), participant=part(1), lp_change=19, rank_label="Diamond III · 38 LP",
        highlights=HIGHLIGHTS, day_number=4, day_limit=10,
    )
    embed = match_embed(notice, settings=make_settings())
    assert embed["title"] == "Mike a gagné 19 LP (Solo/Duo)"
    assert embed["author"]["name"] == "EL PSYY#EUW"
    assert embed["author"]["icon_url"].endswith("/img/profileicon/29.png")
    assert embed["thumbnail"]["url"].endswith("/img/champion/MonkeyKing.png")
    assert embed["color"] == notifications.COLOR_WIN
    assert "**Diamond III · 38 LP**" in embed["description"]
    assert "**Wukong**" in embed["description"]
    assert "PENTAKILL" in embed["description"]
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["KDA"] == "15/4/15 (7,50)"
    assert fields["Durée"] == "32:27"
    assert fields["Score"] == "9,75 (MVP)"
    assert fields["CS/min"] == "7,2 (234)"
    assert fields["Pings"] == "53"
    assert fields["Dégâts"] == "35,5k (1 094/min)"
    assert fields["Vision/min"] == "1,45"
    assert fields["Chance d'équipe"] == "Très bonne"
    assert fields["Écart d'or (voie)"] == "+1 250"
    assert fields["Partie du jour"] == "4e / 10"
    assert "https://dpm.lol/EL%20PSYY-EUW?match=8008590279" in fields["Liens"]
    assert "http://localhost:8000/player/1#match-EUW1_8008590279" in fields["Liens"]
    assert embed["footer"]["text"] == "Pékin Express LoL · Duo Rouge"
    assert embed["timestamp"] == "2026-10-10T12:00:00+00:00"


def test_match_embed_loss_over_quota_and_without_details():
    notice = MatchNotice(
        player=mike(), team=None, participant=part(1, win=False), lp_change=-17, day_number=11, day_limit=10, over_quota=True,
    )
    embed = match_embed(notice, settings=make_settings())
    assert embed["title"] == "Mike a perdu 17 LP (Solo/Duo)"
    assert embed["color"] == notifications.COLOR_LOSS
    assert "Hors quota" in embed["description"] and "11e partie du jour (limite : 10)" in embed["description"]
    names = [f["name"] for f in embed["fields"]]
    assert "Score" not in names and "Pings" not in names  # pas de JSON de partie : stats de base seulement
    assert {"KDA", "Durée", "CS/min", "Dégâts", "Vision/min", "Liens"} <= set(names)

    outside = match_embed(replace(notice, outside_window=True, lp_change=None), settings=make_settings())
    assert "Hors des heures du challenge" in outside["description"]
    assert outside["title"] == "Mike a perdu sa partie (Solo/Duo)"


def test_links_go_through_the_fixed_github_link_once_published(monkeypatch: pytest.MonkeyPatch):
    settings = make_settings(github_token="tok", github_repo="Kazza2115/PekinExpressLoL")
    monkeypatch.setattr(portal.portal_state, "published_url", "https://x.trycloudflare.com")
    monkeypatch.setattr(portal.portal_state, "error", None)
    assert portal.share_link("/dashboard", settings) == "https://kazza2115.github.io/PekinExpressLoL/#/dashboard"
    assert portal.share_link("/", settings) == "https://kazza2115.github.io/PekinExpressLoL/"
    text = notifications.format_challenge_started(Challenge(name="Pékin Express LoL"), settings=settings)
    assert "https://kazza2115.github.io/PekinExpressLoL/#/dashboard" in text


# --------------------------------------------------------------------------- #
# Duo dans la même partie, GIF
# --------------------------------------------------------------------------- #


def test_duo_in_the_same_game_is_grouped():
    rouge = duo_rouge()
    notices = [
        MatchNotice(player=mike(), team=rouge, participant=part(1), lp_change=19),
        MatchNotice(player=lea(), team=rouge, participant=part(2), lp_change=21),
        MatchNotice(player=Player(id=3, display_name="Solo", team_id=None), team=None, participant=part(3)),
        MatchNotice(player=Player(id=4, display_name="Bleu", team_id=2), team=None, participant=part(4, side=200, win=False)),
        MatchNotice(player=mike(), team=rouge, participant=part(1, match_id="EUW1_2")),
    ]
    groups = group_match_notices(notices)
    assert [[n.player.id for n in g] for g in groups] == [[1, 2], [3], [4], [1]]


def test_duo_win_message_has_both_cards_and_the_gif():
    rouge = duo_rouge()
    notices = [
        MatchNotice(player=mike(), team=rouge, participant=part(1), lp_change=19),
        MatchNotice(player=lea(), team=rouge, participant=part(2, champion="Ahri"), lp_change=21),
    ]
    content, embeds = build_match_message(notices, gif="https://static.klipy.com/chad.gif", settings=make_settings())
    assert content == "🎉 **Duo Rouge** (Mike & Léa) gagne en duo · +40 LP"
    assert len(embeds) == 3
    assert embeds[2]["image"] == {"url": "https://static.klipy.com/chad.gif"}
    assert "Victoire en duo" in embeds[2]["title"]

    losses = [replace(n, participant=part(n.player.id, win=False), lp_change=-18) for n in notices]
    content, embeds = build_match_message(losses, gif=None, settings=make_settings())
    assert content == "💀 **Duo Rouge** (Mike & Léa) perd en duo · −36 LP"
    assert len(embeds) == 2  # sans GIF : pas de carte en plus


def test_single_player_message_puts_the_gif_under_the_card():
    notice = MatchNotice(player=mike(), team=duo_rouge(), participant=part(1, win=False), lp_change=-17)
    content, embeds = build_match_message([notice], gif="https://static.klipy.com/monkey.gif", settings=make_settings())
    assert content == "❌ **Mike** (Duo Rouge) perd avec **Wukong** · 15/4/15 · −17 LP"
    assert len(embeds) == 1 and embeds[0]["image"] == {"url": "https://static.klipy.com/monkey.gif"}


# --------------------------------------------------------------------------- #
# Klipy
# --------------------------------------------------------------------------- #


def klipy_body(*urls: str) -> dict[str, Any]:
    items = [{"type": "ad", "content": "<div/>"}]
    items += [{"id": i, "slug": f"s{i}", "title": "t", "type": "gif", "file": {"md": {"gif": {"url": u}}}} for i, u in enumerate(urls)]
    return {"result": True, "data": {"data": items, "current_page": 1, "per_page": 24, "has_next": False}}


async def test_find_gif_searches_a_random_category_on_klipy():
    seen: list[httpx.Request] = []
    settings = make_settings(klipy_api_key="cle-test", gif_search_loss=("Goofy Patrick",))
    async with recorder(seen, 200, klipy_body("https://static.klipy.com/a.gif", "https://static.klipy.com/b.gif")) as client:
        choice = await gifs.find_gif(False, settings=settings, client=client, rng=random.Random(1))
        again = await gifs.find_gif(False, settings=settings, client=client, rng=random.Random(2))
    assert choice is not None and choice.query == "Goofy Patrick"
    assert choice.url in {"https://static.klipy.com/a.gif", "https://static.klipy.com/b.gif"}
    assert again is not None
    assert len(seen) == 1  # résultats en cache
    request = seen[0]
    assert request.url.path == "/api/v1/cle-test/gifs/search"
    assert request.url.params["q"] == "Goofy Patrick"
    assert request.url.params["content_filter"] == "medium"


async def test_find_gif_falls_back_when_klipy_fails_or_has_no_key():
    seen: list[httpx.Request] = []
    settings = make_settings(klipy_api_key="cle", gif_search_win=("Chad",), gif_fallback_win=("https://x.test/win.gif",))
    async with recorder(seen, 401) as client:
        choice = await gifs.find_gif(True, settings=settings, client=client)
    assert choice == gifs.GifChoice(url="https://x.test/win.gif", query=None)
    assert await gifs.find_gif(True, settings=make_settings()) is None  # ni clé ni secours : pas de GIF


def test_extract_gif_urls_prefers_medium_and_skips_ads():
    body = klipy_body("https://static.klipy.com/md.gif")
    body["data"]["data"].append({"type": "gif", "file": {"hd": {"gif": {"url": "https://static.klipy.com/hd.gif"}}}})
    body["data"]["data"].append({"type": "gif", "file": {}})
    assert gifs.extract_gif_urls(body) == ["https://static.klipy.com/md.gif", "https://static.klipy.com/hd.gif"]
    assert gifs.extract_gif_urls({"result": False}) == []


async def test_test_message_previews_a_win_and_a_loss_gif():
    seen: list[httpx.Request] = []
    settings = make_settings(klipy_api_key="cle")
    async with recorder(seen, 200, klipy_body("https://static.klipy.com/a.gif")) as client:
        content, embeds = await notifications.build_test_message(settings=settings, client=client)
    assert content == notifications.TEST_NOTIFICATION_CONTENT
    assert embeds[0]["title"].startswith("Exemple — ")
    assert [e["image"]["url"] for e in embeds[1:]] == ["https://static.klipy.com/a.gif"] * 2
    assert "catégorie Klipy" in embeds[1]["title"]
    _, without_key = await notifications.build_test_message(settings=make_settings())
    assert "KLIPY_API_KEY" in without_key[1]["description"]


# --------------------------------------------------------------------------- #
# Statistiques de la partie (JSON Match-V5)
# --------------------------------------------------------------------------- #


def test_player_highlights_from_match_json():
    parts = [sb_participant(i, 100) for i in range(5)] + [sb_participant(i + 5, 200, kills=1, deaths=6) for i in range(5)]
    parts[0].update(puuid="p-mike", kills=12, deaths=1, assists=8, totalDamageDealtToChampions=40000, basicPings=10, dangerPings=4)
    highlights = player_highlights(match_of(parts, duration=1800), Player(id=1, display_name="Mike", puuid="p-mike"))
    assert highlights is not None
    assert highlights["badge"] == "MVP" and highlights["place"] == 1 and highlights["players_count"] == 10
    assert highlights["pings"] == 14
    assert highlights["damage_per_min"] == round(40000 / 30)
    assert highlights["vision_per_min"] == round(20 / 30, 2)
    assert highlights["team_luck"] == "Très bonne"  # coéquipiers KDA 2,67 contre 1,0 en face
    assert player_highlights(match_of(parts), Player(id=9, display_name="Absent", puuid="p-absent")) is None


def test_team_luck_levels():
    assert team_luck([5, 5], [2, 2]) == "Très bonne"
    assert team_luck([3], [3]) == "Moyenne"
    assert team_luck([1], [3]) == "Très mauvaise"
    assert team_luck([], [3]) is None


# --------------------------------------------------------------------------- #
# Poller : un seul message pour le duo
# --------------------------------------------------------------------------- #


async def test_poller_sends_one_message_for_a_duo_game_and_for_a_duo_live_start(
    session: Session, monkeypatch: pytest.MonkeyPatch
):
    session.add(Challenge(status=ChallengeStatus.RUNNING, start_at=utcnow() - timedelta(hours=2)))
    team = Team(name="Duo Rouge", color="#ef4444", slot=1)
    session.add(team)
    session.commit()
    session.refresh(team)
    make_player(session, "Mike", "p-mike", team_id=team.id)
    make_player(session, "Léa", "p-lea", team_id=team.id)

    sent: list[tuple[str, list[dict[str, Any]] | None]] = []

    async def fake_send(content: str, *, embeds=None, settings=None, **_: Any) -> bool:
        sent.append((content, embeds))
        return True

    async def fake_gif(win: bool, **_: Any) -> gifs.GifChoice:
        return gifs.GifChoice(url="https://static.klipy.com/win.gif" if win else "https://static.klipy.com/loss.gif", query="t")

    monkeypatch.setattr("app.services.poller.send_discord", fake_send)
    monkeypatch.setattr(gifs, "find_gif", fake_gif)

    api = ScriptedAPI()
    api.entries["p-mike"] = gold_iv(50)
    api.entries["p-lea"] = gold_iv(60)
    champions = {"p-mike": 62, "p-lea": 103, "stranger": 86}
    for puuid in ("p-mike", "p-lea"):
        api.active[puuid] = ActiveGameDTO(
            game_id=77, game_start=utcnow(), queue_id=420, game_mode="CLASSIC", champion_id=champions[puuid],
            champion_name=None, champions_by_puuid=champions,
        )

    async def champion_name(champion_id: int) -> str:
        return {62: "MonkeyKing", 103: "Ahri"}.get(champion_id, "Garen")

    monkeypatch.setattr(Poller, "_champion_name", staticmethod(champion_name))
    poller = Poller(api, bus, state, settings=make_settings())

    await poller.poll_once()
    live = [content for content, _ in sent if "lance une partie" in content]
    assert live == ["🔴 **Duo Rouge** (Mike & Léa) lance une partie en duo — **Wukong** & **Ahri**"]

    # La partie se termine : victoire des deux (snapshots de référence pris avant la partie)
    for snapshot in session.exec(select(RankSnapshot)).all():
        snapshot.captured_at = utcnow() - timedelta(hours=1)
        session.add(snapshot)
    session.commit()
    sent.clear()
    api.active = {}
    started = utcnow() - timedelta(minutes=35)
    api.match_ids = {"p-mike": ["EUW1_900"], "p-lea": ["EUW1_900"]}
    api.matches["EUW1_900"] = match_json("EUW1_900", [("p-mike", "MonkeyKing", True), ("p-lea", "Ahri", True)], started, 1800)
    api.entries["p-mike"] = gold_iv(70, wins=11)
    api.entries["p-lea"] = gold_iv(81, wins=11)
    await poller.poll_once()

    results = [(content, embeds) for content, embeds in sent if "en duo" in content]
    assert len(results) == 1
    content, embeds = results[0]
    assert content == "🎉 **Duo Rouge** (Mike & Léa) gagne en duo · +41 LP"
    assert [e["title"] for e in embeds[:2]] == ["Mike a gagné 20 LP (Solo/Duo)", "Léa a gagné 21 LP (Solo/Duo)"]
    assert "**Gold IV · 70 LP**" in embeds[0]["description"]
    assert embeds[2]["image"]["url"] == "https://static.klipy.com/win.gif"


async def test_klipy_outage_backs_off_and_uses_the_fallback():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ConnectError("hors ligne")

    settings = make_settings(klipy_api_key="cle", gif_search_loss=("Monkey", "Goofy Dog"), gif_fallback_loss=("https://x.test/l.gif",))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await gifs.find_gif(False, settings=settings, client=client)
        second = await gifs.find_gif(False, settings=settings, client=client)
    assert first == second == gifs.GifChoice(url="https://x.test/l.gif")
    assert len(seen) == 1  # une seule tentative, puis pause de 5 min


async def test_klipy_empty_results_try_another_category():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["q"] == "Chad":
            return httpx.Response(200, json=klipy_body())
        return httpx.Response(200, json=klipy_body("https://static.klipy.com/drake.gif"))

    settings = make_settings(klipy_api_key="cle", gif_search_win=("Chad", "Goofy Drake"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for seed in range(6):
            gifs.reset_cache()
            choice = await gifs.find_gif(True, settings=settings, client=client, rng=random.Random(seed))
            assert choice == gifs.GifChoice(url="https://static.klipy.com/drake.gif", query="Goofy Drake")


def test_headline_keeps_the_first_line():
    assert notifications.headline("🎡 **Les duos sont tirés !**\n• Duo Rouge : A & B") == "🎡 **Les duos sont tirés !**"
    assert notifications.headline("") == ""
