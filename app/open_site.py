"""Ouvre le navigateur au lancement de `PekinExpress.bat` sur la bonne adresse.

Le lien fixe GitHub Pages (`https://<compte>.github.io/<dépôt>/`) dès que la nouvelle adresse du
tunnel y est publiée (l'ouvrir plus tôt renverrait vers l'adresse du lancement précédent, morte).
Sans lien fixe configuré (pas de `GITHUB_TOKEN`) ou s'il n'est pas prêt à temps : l'adresse du
tunnel ; sans tunnel (`--local`, `AUTO_TUNNEL=false`) : l'adresse locale.

Lancé sans fenêtre (`pythonw -m app.open_site`) : uniquement la bibliothèque standard, et lit
l'état du site par `GET /api/state`.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from typing import Any

LOCAL_URL = "http://127.0.0.1:8000"
SERVER_TIMEOUT_S = 180  # démarrage du serveur (installation des dépendances comprise)
PORTAL_TIMEOUT_S = 150  # tunnel + publication sur GitHub
TUNNEL_TIMEOUT_S = 60  # sans lien fixe : attente de l'adresse du tunnel
POLL_S = 2.0

State = dict[str, Any]


def fetch_state(timeout: float = 3.0) -> State | None:
    """État du site (`/api/state`) ; None s'il ne répond pas encore."""
    try:
        with urllib.request.urlopen(f"{LOCAL_URL}/api/state", timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _strip(url: str | None) -> str:
    return (url or "").rstrip("/")


def portal_ready(state: State) -> bool:
    """Le lien fixe renvoie vers l'adresse actuelle du tunnel, vérifiée et publiée."""
    portal = state.get("portal") or {}
    public = state.get("public_url") or {}
    return bool(
        portal.get("enabled")
        and portal.get("portal_url")
        and portal.get("published_url")
        and not portal.get("waiting")
        and not portal.get("error")
        and _strip(portal.get("published_url")) == _strip(public.get("url"))
    )


def tunnel_url(state: State) -> str | None:
    public = state.get("public_url") or {}
    return public.get("url") if public.get("source") in ("tunnel", "env") else None


def choose_url(
    *,
    local_only: bool = False,
    fetch: Callable[[], State | None] = fetch_state,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Adresse à ouvrir : attend le serveur, puis le lien fixe (ou le tunnel), sinon le local."""
    start = clock()
    state = fetch()
    while state is None and clock() - start < SERVER_TIMEOUT_S:
        sleep(POLL_S)
        state = fetch()
    if state is None or local_only:
        return LOCAL_URL

    portal_on = bool((state.get("portal") or {}).get("enabled"))
    deadline = clock() + (PORTAL_TIMEOUT_S if portal_on else TUNNEL_TIMEOUT_S)
    while True:
        if portal_ready(state):
            return state["portal"]["portal_url"]
        portal = state.get("portal") or {}
        if not portal_on or portal.get("error"):
            # Pas de lien fixe (ou jeton refusé) : l'adresse du tunnel dès qu'elle existe
            url = tunnel_url(state)
            if url:
                return url
        if clock() >= deadline:
            return tunnel_url(state) or LOCAL_URL
        sleep(POLL_S)
        state = fetch() or state


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    webbrowser.open(choose_url(local_only="--local" in args))


if __name__ == "__main__":
    main()
