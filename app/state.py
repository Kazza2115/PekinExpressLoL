"""État en mémoire partagé entre le poller et l'API (parties en cours, dernier cycle)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class LiveGameState:
    player_id: int
    game_id: int
    champion_id: int
    champion_name: str
    queue_id: int
    game_mode: str
    game_start: datetime  # UTC
    detected_at: datetime  # UTC : premier poll où la partie a été vue

    def elapsed_seconds(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        start = self.game_start if self.game_start.timestamp() > 0 else self.detected_at
        return max(0, int((now - start).total_seconds()))


@dataclass
class PollReport:
    started_at: datetime
    duration_s: float = 0.0
    requests: int = 0
    players_polled: int = 0
    new_matches: int = 0
    new_snapshots: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "duration_s": round(self.duration_s, 2),
            "requests": self.requests,
            "players_polled": self.players_polled,
            "new_matches": self.new_matches,
            "new_snapshots": self.new_snapshots,
            "errors": list(self.errors),
        }


@dataclass
class AppState:
    live_games: dict[int, LiveGameState] = field(default_factory=dict)  # player_id → partie
    last_poll: PollReport | None = None
    poll_count: int = 0
    polling: bool = False  # un cycle est en cours
    last_live_check: datetime | None = None  # dernière détection des parties en cours


state = AppState()
