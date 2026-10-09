"""Adresse publique du site.

`Tunnel.bat` écrit l'adresse du tunnel dans `data/tunnel.log` : `https://xxx.trycloudflare.com`
(mode rapide, différente à chaque lancement) ou `https://nom-du-pc.xxxx.ts.net` (Tailscale
Funnel, lien fixe). On la lit ici pour l'afficher dans l'Admin et l'utiliser dans les messages
Discord quand `BASE_URL` est resté sur une adresse locale. Un tunnel Cloudflare nommé (lien fixe
sur un domaine) n'écrit pas son adresse : elle vient de `BASE_URL`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT, Settings, get_settings

TUNNEL_LOG = PROJECT_ROOT / "data" / "tunnel.log"
TUNNEL_URL_RE = re.compile(r"https://(?:[a-z0-9-]+\.trycloudflare\.com|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net)")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


@dataclass
class PublicUrl:
    url: str
    source: str  # "env" (BASE_URL), "tunnel" (journal du tunnel) ou "local"
    detected_at: str | None = None  # date du journal du tunnel

    @property
    def fixed(self) -> bool:
        """Adresse stable d'un lancement à l'autre (tout sauf le mode rapide trycloudflare.com)."""
        return self.source != "local" and not self.url.lower().rstrip("/").endswith(".trycloudflare.com")

    def to_dict(self) -> dict:
        return {"url": self.url, "source": self.source, "detected_at": self.detected_at, "fixed": self.fixed}


def is_local_url(url: str) -> bool:
    lowered = url.lower()
    return any(f"//{host}" in lowered for host in LOCAL_HOSTS)


def detect_tunnel_url(log_path: Path = TUNNEL_LOG) -> tuple[str, str] | None:
    """Dernière adresse de tunnel du journal (trycloudflare.com ou ts.net), avec la date du fichier."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        stamp = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    matches = TUNNEL_URL_RE.findall(text)
    if not matches:
        return None
    return matches[-1], stamp.isoformat()


def public_url(settings: Settings | None = None, log_path: Path = TUNNEL_LOG) -> PublicUrl:
    """`BASE_URL` si elle est publique, sinon l'adresse du tunnel détectée, sinon l'adresse locale."""
    settings = settings or get_settings()
    if settings.base_url and not is_local_url(settings.base_url):
        return PublicUrl(settings.base_url, "env")
    detected = detect_tunnel_url(log_path)
    if detected:
        return PublicUrl(detected[0], "tunnel", detected[1])
    return PublicUrl(settings.base_url, "local")


def effective_base_url(settings: Settings | None = None) -> str:
    return public_url(settings).url
