"""Adresse publique du site.

`Tunnel.bat` lance cloudflared avec `--logfile data/tunnel.log` : l'adresse
`https://xxx.trycloudflare.com` attribuée (différente à chaque lancement) y est écrite.
On la lit ici pour l'afficher dans l'Admin et l'utiliser dans les messages Discord quand
`BASE_URL` est resté sur une adresse locale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT, Settings, get_settings

TUNNEL_LOG = PROJECT_ROOT / "data" / "tunnel.log"
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


@dataclass
class PublicUrl:
    url: str
    source: str  # "env" (BASE_URL), "tunnel" (journal cloudflared) ou "local"
    detected_at: str | None = None  # date du journal du tunnel

    def to_dict(self) -> dict:
        return {"url": self.url, "source": self.source, "detected_at": self.detected_at}


def is_local_url(url: str) -> bool:
    lowered = url.lower()
    return any(f"//{host}" in lowered for host in LOCAL_HOSTS)


def detect_tunnel_url(log_path: Path = TUNNEL_LOG) -> tuple[str, str] | None:
    """Dernière adresse trycloudflare.com du journal, avec la date de modification du fichier."""
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
