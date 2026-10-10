"""Polling des événements côté navigateur (app/static/js/app.js), exécuté dans node avec un DOM factice.

Le bus d'événements (app/events.py) repart à id=1 à chaque démarrage du processus : après une relance
de PekinExpress.bat (--reload, plantage, reboot), un onglet déjà ouvert doit se recaler sur le nouveau
compteur, sinon `since` reste figé et plus aucun toast / notification « lance une partie » n'arrive.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app" / "static" / "js" / "app.js"
HARNESS = Path(__file__).with_name("js") / "poll_restart_harness.js"
NODE = shutil.which("node")


def _run_harness() -> dict:
    assert NODE is not None
    result = subprocess.run(
        [NODE, str(HARNESS), str(APP_JS)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node introuvable : test du front ignoré")
def test_poll_follows_new_counter_after_server_restart() -> None:
    """Onglet ouvert avec since=230 ; le serveur redémarre (compteur à 3) puis publie un live_start (id 4)."""
    out = _run_harness()
    # Avant : calage sur le dernier id (230) sans rejeu. Après le redémarrage : on repart du
    # compteur du nouveau processus (3) au lieu de rester figé sur 230 pendant des heures.
    assert out["polls"] == [None, 230, 230, 3, 5], out["polls"]
    assert "live_start" in out["dispatched"] and "rank_changed" in out["dispatched"], out["dispatched"]
    assert any("Alice" in t and "lance une partie" in t for t in out["toasts"]), out["toasts"]
    assert any("Alice" in n["title"] for n in out["notifs"]), out["notifs"]
    # Même version des fichiers statiques : pas de rechargement automatique (c'est bien le polling
    # qui doit se recaler, pas `hello`).
    assert out["reloaded"] is False


# ---------------------------------------------------------------------------
# « Supprimer les joueurs de démo » (DELETE /api/admin/players/demo/all) publie `teams_changed`, plus
# `challenge_reset` : ce dernier déclenchait sur toutes les pages le toast « ♻️ Le challenge a été
# réinitialisé. » alors que le statut n'avait pas bougé (duos figés, inscriptions closes).
# ---------------------------------------------------------------------------
STATIC_JS = ROOT / "app" / "static" / "js"


def _connect_events_block(name: str) -> str:
    """Contenu du bloc `App.connectEvents({ … })` d'un script de page (accolades appariées)."""
    source = (STATIC_JS / name).read_text(encoding="utf-8")
    start = source.find("App.connectEvents({")
    assert start >= 0, f"{name} : App.connectEvents introuvable"
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"{name} : bloc App.connectEvents non fermé")


@pytest.mark.parametrize("name", ["home.js", "dashboard.js", "player.js", "duos.js", "admin.js"])
def test_pages_reload_on_teams_changed_like_on_challenge_reset(name: str) -> None:
    """Toute page qui se recharge sur `challenge_reset` doit aussi se recharger sur `teams_changed`,
    sinon elle garde les joueurs / duos démo affichés après leur suppression jusqu'au prochain
    rafraîchissement périodique."""
    block = _connect_events_block(name)
    assert "challenge_reset:" in block, name
    assert "teams_changed:" in block, name


def test_no_global_toast_on_teams_changed() -> None:
    """Aucun toast global (app.js) sur `teams_changed` : la suppression des joueurs démo reste silencieuse
    hors Admin ; seul le vrai « Réinitialiser » (`challenge_reset`) annonce la réinitialisation."""
    source = APP_JS.read_text(encoding="utf-8")
    assert "App.onEvent('challenge_reset'" in source
    assert "App.onEvent('teams_changed'" not in source


# ---------------------------------------------------------------------------
# Admin → Duos, challenge terminé : l'avis « 🔒 … les duos sont figés. » doit dire quoi faire (comme
# celui du challenge en cours), sinon l'organisateur reste bloqué sans savoir que seul
# « Réinitialiser » (en gardant les joueurs inscrits) rouvre la composition des duos.
# ---------------------------------------------------------------------------
def test_admin_finished_duos_notice_names_the_way_out() -> None:
    source = (STATIC_JS / "admin.js").read_text(encoding="utf-8")
    start = source.index("Le challenge est terminé : les duos sont figés.")
    notice = source[start : source.index("\n", start)]
    assert "Réinitialiser" in notice, notice
    assert "joueurs inscrits" in notice, notice



# ---------------------------------------------------------------------------
# Accueil (home.js) : l'inscription et « Lier mon compte » suivent REGISTRATION_OPEN_STATUSES de
# l'API (registration + drawn). Avant, home.js ne considérait ouvert que `registration` : après
# « Former les duos au hasard » (statut drawn) puis suppression des joueurs, l'accueil affichait
# « Les inscriptions sont closes. » (formulaire et boutons cachés) alors que POST /api/players répondait
# 201 — et rien ne ramenait aux inscriptions sauf « Réinitialiser ».
# ---------------------------------------------------------------------------
HOME_HARNESS = Path(__file__).with_name("js") / "home_layout_harness.js"
HOME_JS = STATIC_JS / "home.js"
PLAYERS_HINT_OPEN = "Chaque joueur lie son compte LoL pour être suivi."
PLAYERS_HINT_CLOSED = "Les inscriptions sont closes."


def _render_home(status: str) -> dict:
    assert NODE is not None
    result = subprocess.run(
        [NODE, str(HOME_HARNESS), str(APP_JS), str(HOME_JS), status],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "/api/state" in out["requested"], out["requested"]
    assert "Impossible de charger" not in out["slotsHtml"], out["slotsHtml"]
    return out


@pytest.mark.skipif(NODE is None, reason="node introuvable : test du front ignoré")
@pytest.mark.parametrize("status", ["registration", "drawn"])
def test_home_keeps_registration_open_while_the_api_accepts_it(status: str) -> None:
    out = _render_home(status)
    assert out["registerHidden"] is False, out
    assert out["sideHidden"] is False and out["demoHidden"] is False, out
    assert out["homeLayout"] is True, out
    assert out["playersHint"] == PLAYERS_HINT_OPEN, out["playersHint"]
    # Le retardataire (inscrit sans compte) a son formulaire « Lier mon compte »
    assert out["linkForms"] == 1 and "Lier mon compte" in out["slotsHtml"], out["slotsHtml"]
    assert "3</strong> / 8 joueurs" in out["countHtml"], out["countHtml"]
    if status == "drawn":
        # Le gros bouton de l'organisateur reste là : les duos tirés attendent le départ
        assert 'id="cta-start"' in out["ctaHtml"], out["ctaHtml"]


@pytest.mark.skipif(NODE is None, reason="node introuvable : test du front ignoré")
def test_home_keeps_registration_open_during_the_challenge_for_late_players() -> None:
    out = _render_home("running")
    assert out["registerHidden"] is False and out["homeLayout"] is True, out
    assert "en retard" in out["playersHint"], out["playersHint"]
    assert out["linkForms"] == 1 and "Lier mon compte" in out["slotsHtml"], out["slotsHtml"]


@pytest.mark.skipif(NODE is None, reason="node introuvable : test du front ignoré")
@pytest.mark.parametrize("status", ["finished"])
def test_home_closes_registration_once_the_challenge_is_over(status: str) -> None:
    out = _render_home(status)
    assert out["registerHidden"] is True and out["sideHidden"] is True, out
    assert out["homeLayout"] is False, out
    assert out["playersHint"] == PLAYERS_HINT_CLOSED, out["playersHint"]
    assert out["linkForms"] == 0 and "Lier mon compte" not in out["slotsHtml"], out["slotsHtml"]


def test_admin_drawn_without_players_notice_names_the_way_out() -> None:
    """Admin → Duos, statut « Duos formés » sans plus aucun joueur : l'avis doit dire que l'accueil
    accepte encore les inscriptions et que « Réinitialiser » ramène aux inscriptions."""
    source = (STATIC_JS / "admin.js").read_text(encoding="utf-8")
    start = source.index("Aucun joueur inscrit :")
    notice = source[start : source.index("\n", start)]
    assert "accueil" in notice and "Réinitialiser" in notice, notice
    guard = source[source.rindex("\n", 0, start) - 200 : start]
    assert "status === 'drawn'" in guard and "!readOnly" in guard, guard


# ---------------------------------------------------------------------------
# Admin → Duos, « 🎲 Former les duos au hasard » : avec deux joueurs (un seul duo) le toast disait
# « 1 duos formés au hasard. » (pluriel figé). On évalue dans node le bloc de succès du gestionnaire
# de clic (celui qui construit le message) avec la réponse de POST /api/admin/teams/auto.
# ---------------------------------------------------------------------------
def _team_auto_success_block() -> str:
    """Corps du `if (r !== undefined) { … }` du clic sur « Former les duos au hasard » (admin.js)."""
    source = (STATIC_JS / "admin.js").read_text(encoding="utf-8")
    start = source.index("els.btnTeamAuto.addEventListener(")
    guard = source.index("if (r !== undefined) {", start)
    opening = source.index("{", guard)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError("admin.js : bloc de succès de btnTeamAuto non fermé")


def _team_auto_toasts(response: dict) -> list[str]:
    assert NODE is not None
    script = (
        "const toasts = []; const toast = (m) => toasts.push(m);"
        "const r = JSON.parse(process.argv[1]);"
        "(function () {" + _team_auto_success_block() + "})();"
        "console.log(JSON.stringify(toasts));"
    )
    result = subprocess.run(
        [NODE, "-e", script, json.dumps(response)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node introuvable : test du front ignoré")
@pytest.mark.parametrize(
    ("teams", "expected"),
    [
        ([{"id": 1}], "1 duo formé au hasard."),  # deux joueurs inscrits : le cas de l'organisateur
        ([{"id": 1}, {"id": 2}], "2 duos formés au hasard."),
        ([{"id": 1}, {"id": 2}, {"id": 3}], "3 duos formés au hasard."),
    ],
)
def test_team_auto_toast_agrees_in_number(teams: list[dict], expected: str) -> None:
    assert _team_auto_toasts({"order": [], "teams": teams}) == [expected]


@pytest.mark.skipif(NODE is None, reason="node introuvable : test du front ignoré")
def test_team_auto_toast_keeps_a_generic_sentence_without_teams() -> None:
    """Réponse sans `teams` (cas défensif, injoignable aujourd'hui) : phrase générique, pas « 0 duo »."""
    assert _team_auto_toasts({"order": []}) == ["Les duos ont été formés au hasard."]
