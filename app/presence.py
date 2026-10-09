"""Qui a le site ouvert en ce moment : registre en mémoire, alimenté par les pages.

Chaque page interroge déjà `/api/events/recent` toutes les 5 s (20 s en arrière-plan) : elle y
joint un identifiant de navigateur (`cid`, le même pour tous ses onglets), un identifiant
d'onglet, la page affichée et, si la personne l'a choisi, le joueur qu'elle dit être. Rien n'est
stocké sur disque, ni adresse IP, ni navigateur : un visiteur disparaît quelques dizaines de
secondes après avoir fermé ses onglets. L'identité est déclarative (pas de connexion) : elle sert
à l'affichage, jamais à autoriser quoi que ce soit.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

# Un onglet visible interroge le serveur toutes les 5 s, un onglet en arrière-plan toutes les 20 s
# (et les navigateurs ralentissent encore les onglets cachés) : délais d'oubli correspondants.
TTL_VISIBLE_S = 30.0
TTL_HIDDEN_S = 75.0
# Garde-fous contre le remplissage : nombre de navigateurs suivis, d'onglets par navigateur
MAX_CLIENTS = 200
MAX_TABS_PER_CLIENT = 8
PAGES = frozenset({"home", "duos", "dashboard", "rankings", "player", "admin"})
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass
class _Tab:
    page: str
    visible: bool
    seen: float


@dataclass
class _Client:
    player_id: int | None
    seen: float
    tabs: dict[str, _Tab] = field(default_factory=dict)


@dataclass
class PresenceSnapshot:
    """Vue agrégée : joueurs identifiés (un seul par joueur, tous appareils confondus) et visiteurs."""

    players: dict[int, dict] = field(default_factory=dict)  # player_id → {active, page}
    anonymous: int = 0
    anonymous_active: int = 0


class PresenceRegistry:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, _Client] = {}
        self._clock = clock

    @staticmethod
    def valid_id(value: str | None) -> bool:
        return bool(value) and bool(_ID_RE.match(value or ""))

    def touch(self, cid: str | None, tab: str | None, page: str | None, player_id: int | None, visible: bool) -> bool:
        """Signale qu'un onglet est ouvert. Identifiants invalides : ignoré (renvoie False)."""
        if not self.valid_id(cid):
            return False
        tab_id = tab if self.valid_id(tab) else "default"
        page_name = page if page in PAGES else "other"
        now = self._clock()
        with self._lock:
            self._prune(now)
            client = self._clients.get(cid)  # type: ignore[arg-type]
            if client is None:
                if len(self._clients) >= MAX_CLIENTS:
                    stalest = min(self._clients, key=lambda key: self._clients[key].seen)
                    del self._clients[stalest]
                client = self._clients[cid] = _Client(player_id=player_id, seen=now)  # type: ignore[index]
            client.player_id = player_id
            client.seen = now
            if tab_id not in client.tabs and len(client.tabs) >= MAX_TABS_PER_CLIENT:
                del client.tabs[min(client.tabs, key=lambda key: client.tabs[key].seen)]
            client.tabs[tab_id] = _Tab(page=page_name, visible=visible, seen=now)
        return True

    def leave(self, cid: str | None, tab: str | None) -> None:
        """Onglet fermé (ou quitté) : on l'oublie tout de suite."""
        if not self.valid_id(cid):
            return
        with self._lock:
            client = self._clients.get(cid)  # type: ignore[arg-type]
            if client is None:
                return
            client.tabs.pop(tab if self.valid_id(tab) else "default", None)  # type: ignore[arg-type]
            if not client.tabs:
                del self._clients[cid]  # type: ignore[arg-type]

    def snapshot(self) -> PresenceSnapshot:
        now = self._clock()
        snap = PresenceSnapshot()
        with self._lock:
            self._prune(now)
            for client in self._clients.values():
                visible_tabs = [t for t in client.tabs.values() if t.visible]
                active = bool(visible_tabs)
                latest = max(visible_tabs or client.tabs.values(), key=lambda t: t.seen)
                if client.player_id is None:
                    snap.anonymous += 1
                    snap.anonymous_active += int(active)
                    continue
                current = snap.players.get(client.player_id)
                if current is None or (active and not current["active"]):
                    snap.players[client.player_id] = {"active": active, "page": latest.page}
        return snap

    def clear(self) -> None:
        with self._lock:
            self._clients.clear()

    def _prune(self, now: float) -> None:
        for cid in list(self._clients):
            client = self._clients[cid]
            client.tabs = {
                key: tab
                for key, tab in client.tabs.items()
                if now - tab.seen <= (TTL_VISIBLE_S if tab.visible else TTL_HIDDEN_S)
            }
            if not client.tabs:
                del self._clients[cid]


presence = PresenceRegistry()
