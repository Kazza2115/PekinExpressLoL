"""Version du site installé : empreinte des fichiers statiques + commit GitHub appliqué.

- `ASSET_VERSION` : hash des fichiers de `app/static` au démarrage. Ajouté en `?v=` sur les
  CSS/JS et envoyé aux navigateurs (SSE `hello`) : s'il change, la page se recharge.
- `data/version.json` : écrit par le service de mise à jour (`app/services/updater.py`)
  avec le commit GitHub installé.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT

STATIC_DIR = Path(__file__).resolve().parent / "static"


def compute_asset_version(static_dir: Path = STATIC_DIR) -> str:
    """Empreinte courte (10 caractères) des fichiers statiques (nom, taille, date)."""
    digest = hashlib.sha1()
    for file in sorted(static_dir.rglob("*")):
        if file.is_file():
            stat = file.stat()
            digest.update(str(file.relative_to(static_dir)).encode("utf-8"))
            digest.update(str(stat.st_mtime_ns).encode("utf-8"))
            digest.update(str(stat.st_size).encode("utf-8"))
    return digest.hexdigest()[:10]


ASSET_VERSION = compute_asset_version()


def site_version_label() -> str:
    """Version lisible, affichée en bas des pages : « 7 oct. 11:05 · abc1234 » (commit git)
    ou « 7 oct. 11:05 » (date du fichier le plus récent du site, ex. installation ZIP)."""
    import subprocess

    stamp: datetime | None = None
    sha = ""
    git_dir = PROJECT_ROOT / ".git"
    if git_dir.exists():
        try:
            out = subprocess.run(
                ["git", "log", "-1", "--format=%h %cI"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5
            )
            if out.returncode == 0 and out.stdout.strip():
                sha, iso = out.stdout.strip().split(" ", 1)
                stamp = datetime.fromisoformat(iso)
        except (OSError, ValueError, subprocess.SubprocessError):
            stamp = None
    if stamp is None:
        newest = 0.0
        app_dir = Path(__file__).resolve().parent
        for file in app_dir.rglob("*"):
            if file.is_file() and file.suffix in {".py", ".js", ".css", ".html"} and "__pycache__" not in file.parts:
                newest = max(newest, file.stat().st_mtime)
        if newest:
            stamp = datetime.fromtimestamp(newest, tz=timezone.utc)
    if stamp is None:
        return "inconnue"
    try:
        from zoneinfo import ZoneInfo

        from app.config import get_settings

        local = stamp.astimezone(ZoneInfo(get_settings().timezone))
    except Exception:  # noqa: BLE001
        local = stamp
    months = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
    label = f"{local.day} {months[local.month - 1]} {local:%H:%M}"
    return f"{label} · {sha}" if sha else label


SITE_VERSION = site_version_label()
