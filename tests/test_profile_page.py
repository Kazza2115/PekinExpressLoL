"""Fiche joueur (front) : la page charge profile.css et ses conteneurs ; player.js couvre toutes les
statistiques classées et tous les records renvoyés par l'API."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.stats import RANKING_METRICS, RECORD_KEYS
from tests.test_api import MIKE, _register

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
PLAYER_JS = (STATIC / "js" / "player.js").read_text(encoding="utf-8")

CONTAINER_IDS = (
    "profile", "profile-level", "profile-chips", "pf-ranks", "tiles", "pf-groups", "pf-splits",
    "pf-records", "pf-duo", "champions", "lp-chart", "matches",
)


def test_player_page_links_profile_css_and_containers(client: TestClient) -> None:
    player = _register(client, *MIKE)["player"]
    html = client.get(f"/player/{player['id']}").text
    assert re.search(r'href="/static/css/profile\.css\?v=[^"]+"', html)
    assert html.index("/static/css/app.css") < html.index("/static/css/profile.css")
    for element_id in CONTAINER_IDS:
        assert f'id="{element_id}"' in html, element_id


def test_player_js_covers_every_ranking_metric_and_record() -> None:
    for key, _higher_is_better in RANKING_METRICS:
        assert f"['{key}'," in PLAYER_JS, key
    for key in RECORD_KEYS:
        assert f"['{key}'," in PLAYER_JS, key


def test_profile_css_exists() -> None:
    css = (STATIC / "css" / "profile.css").read_text(encoding="utf-8")
    for selector in (".pf-hero", ".pf-rank-chip.is-first", ".pf-stat", ".pf-split-row", ".pf-record", ".pf-champ", ".pf-m-extra"):
        assert selector in css, selector
