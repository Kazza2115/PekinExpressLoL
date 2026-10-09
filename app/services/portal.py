"""Lien fixe gratuit : une page GitHub Pages qui renvoie vers l'adresse actuelle du site.

Le site tourne sur le PC de l'organisateur et le tunnel Cloudflare rapide lui donne une adresse
`https://xxx.trycloudflare.com` différente à chaque lancement. La page `docs/index.html`, servie
par GitHub Pages à une adresse qui ne change jamais (`https://<compte>.github.io/<dépôt>/`), lit
`docs/site.json` et redirige aussitôt vers l'adresse qui y est écrite : ensuite tout passe
directement par le tunnel, sans ralentissement.

Ce module tient `docs/site.json` à jour : dès que l'adresse publique du site change (nouveau
tunnel), il l'écrit sur GitHub par l'API (un commit), avec le jeton `GITHUB_TOKEN` de `.env`.
Sans jeton, rien n'est envoyé. Le jeton n'est jamais journalisé ni exposé par l'API du site.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings
from app.services.tunnel import public_url

log = logging.getLogger("pekin.portal")

GITHUB_API = "https://api.github.com"
SITE_FILE = "docs/site.json"
SYNC_INTERVAL_S = 30
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class PortalError(Exception):
    """Publication impossible ; le message (en français) est affiché dans l'Admin."""


@dataclass
class PortalState:
    enabled: bool = False  # jeton GitHub renseigné
    portal_url: str | None = None  # lien fixe à partager (GitHub Pages)
    published_url: str | None = None  # adresse du site écrite dans docs/site.json
    published_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


portal_state = PortalState()


def portal_page_url(repo: str) -> str:
    """`Kazza2115/PekinExpressLoL` → `https://kazza2115.github.io/PekinExpressLoL/`."""
    owner, _, name = repo.strip().partition("/")
    return f"https://{owner.lower()}.github.io/{name}/"


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pekin-express-lol",
    }


def _error_for(response: httpx.Response) -> PortalError:
    if response.status_code in (401, 403):
        return PortalError(
            "Jeton GitHub refusé (GITHUB_TOKEN) : vérifie qu'il n'a pas expiré et qu'il a l'accès "
            "« Contents » en lecture et écriture sur le dépôt."
        )
    if response.status_code == 404:
        return PortalError("Dépôt ou branche introuvable sur GitHub (GITHUB_REPO / GITHUB_BRANCH) ou jeton sans accès.")
    return PortalError(f"GitHub a répondu {response.status_code} à la publication du lien.")


async def publish_site_url(
    url: str, *, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> bool:
    """Écrit `{"url": …}` dans `docs/site.json` sur GitHub. Renvoie False si c'était déjà la bonne adresse.

    Lève `PortalError` (message français) si GitHub refuse ; jamais d'autre exception réseau.
    """
    settings = settings or get_settings()
    endpoint = f"{GITHUB_API}/repos/{settings.github_repo}/contents/{SITE_FILE}"
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    try:
        for _attempt in range(2):  # un second essai si le fichier a changé entre lecture et écriture
            current = await http.get(endpoint, params={"ref": settings.github_branch}, headers=_headers(settings))
            sha: str | None = None
            if current.status_code == 200:
                body = current.json()
                sha = body.get("sha")
                try:
                    existing = json.loads(base64.b64decode(body.get("content") or "").decode("utf-8") or "{}")
                except ValueError:
                    existing = {}
                if isinstance(existing, dict) and existing.get("url") == url:
                    return False
            elif current.status_code != 404:
                raise _error_for(current)
            payload = {
                "url": url,
                "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            data: dict[str, Any] = {
                "message": f"Lien du site : {url}",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": settings.github_branch,
            }
            if sha:
                data["sha"] = sha
            written = await http.put(endpoint, json=data, headers=_headers(settings))
            if written.status_code in (200, 201):
                return True
            if written.status_code in (409, 422):
                continue  # sha périmé : on relit et on réessaie une fois
            raise _error_for(written)
        raise PortalError("GitHub a refusé la mise à jour du lien (conflit), nouvel essai dans 30 s.")
    except httpx.HTTPError as exc:
        raise PortalError(f"GitHub injoignable ({type(exc).__name__}), nouvel essai dans 30 s.") from exc
    finally:
        if own_client:
            await http.aclose()


async def sync_portal_once(
    settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> None:
    """Publie l'adresse publique actuelle si elle a changé depuis la dernière publication réussie."""
    settings = settings or get_settings()
    portal_state.enabled = bool(settings.github_token)
    portal_state.portal_url = portal_page_url(settings.github_repo)
    if not settings.github_token:
        return
    current = public_url(settings)
    if current.source == "local":
        return  # pas encore de tunnel : rien à publier
    if current.url == portal_state.published_url and portal_state.error is None:
        return
    try:
        changed = await publish_site_url(current.url, settings=settings, client=client)
    except PortalError as exc:
        if str(exc) != portal_state.error:
            log.warning("Lien fixe GitHub Pages non mis à jour : %s", exc)
        portal_state.error = str(exc)
        return
    portal_state.published_url = current.url
    portal_state.published_at = datetime.now(timezone.utc).isoformat()
    portal_state.error = None
    if changed:
        log.info("Lien fixe %s → %s", portal_state.portal_url, current.url)


async def run_portal_sync() -> None:
    """Boucle de fond (démarrée par `app.main`) : vérifie l'adresse toutes les 30 s. Ne crashe jamais."""
    while True:
        try:
            await sync_portal_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Synchronisation du lien fixe en échec")
        await asyncio.sleep(SYNC_INTERVAL_S)


def share_url(settings: Settings | None = None) -> str:
    """Adresse à partager (messages Discord) : le lien fixe GitHub Pages dès qu'il est publié,
    sinon l'adresse publique du moment (tunnel ou BASE_URL)."""
    settings = settings or get_settings()
    if settings.github_token and portal_state.published_url and portal_state.error is None:
        return portal_page_url(settings.github_repo).rstrip("/")
    return public_url(settings).url
