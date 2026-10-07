"""Version du site installé : empreinte des fichiers statiques + commit GitHub appliqué.

- `ASSET_VERSION` : hash des fichiers de `app/static` au démarrage. Ajouté en `?v=` sur les
  CSS/JS et envoyé aux navigateurs (SSE `hello`) : s'il change, la page se recharge.
- `data/version.json` : écrit par le service de mise à jour (`app/services/updater.py`)
  avec le commit GitHub installé.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

STATIC_DIR = Path(__file__).resolve().parent / "static"
VERSION_FILE = PROJECT_ROOT / "data" / "version.json"


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


def read_local_version(path: Path = VERSION_FILE) -> dict[str, Any] | None:
    """Commit installé (`{"sha", "date", "message", "updated_at"}`) ou None (installation ZIP initiale)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("sha") else None


def write_local_version(sha: str, *, date: str | None, message: str | None, path: Path = VERSION_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sha": sha,
        "date": date,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
