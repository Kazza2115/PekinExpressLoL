"""Client Riot simulé (`DemoRiotClient`, implémente `RiotAPI`) : aucun réseau.

Monde simulé, piloté par l'horloge réelle (`time.time()`) et avancé à chaque appel
de `get_active_game` / `get_match_ids_by_puuid` (`tick`), joueur par joueur :

- un joueur hors partie démarre une partie avec la probabilité `start_chance` ;
  elle dure `game_duration_range` secondes (temps réel) et reste visible en
  Spectator au moins un tick complet (le poller doit pouvoir la voir) ;
- à la fin, une partie Match-V5 complète est générée (10 participants, stats
  plausibles), les LP (±14..26) sont appliqués au rang via `stats.absolute_lp` /
  `rank_from_absolute_lp` (promotion / rétrogradation), wins/losses incrémentés ;
  ~5 % de remakes (180 s, LP 0) ; un tick de pause avant la partie suivante.

Horodatages des parties générées : la partie *se termine* à l'instant réel de la
fin de simulation (`gameEndTimestamp`), avec une durée fictive plausible
(`gameDuration` ≈ 15–28 min) et donc un `gameStartTimestamp` dans le passé, de
sorte que la fin de partie corresponde au moment où le rang change (c'est ce que
le poller utilise pour calculer `lp_change`). Le filtre `start_time` de
`get_match_ids_by_puuid` s'applique à la *fin* de partie : les premières parties
d'un challenge (commencées fictivement avant « Démarrer ») sont donc renvoyées,
et comptent puisque le site place une partie dans la fenêtre d'après sa fin.

`puuid` déterministe : "demo-" + sha1(riot_id)[:24] ; rang initial déterministe
par puuid (Silver IV → Diamond II). Le Riot ID "unranked#…" simule un compte
jamais classé (aucune entrée League-V4).
"""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.riot.base import AccountDTO, ActiveGameDTO, LeagueEntryDTO, RiotNotFound, SummonerDTO
from app.services.stats import absolute_lp, rank_from_absolute_lp

RANKED_SOLO_QUEUE_ID = 420
POSITIONS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
DEMO_GAME_VERSION = "14.24.650.2384"
# Une partie reste en cours au moins ce nombre de ticks après celui qui l'a démarrée
MIN_LIVE_TICKS = 2
# Nombre maximal de parties conservées en mémoire (les plus anciennes sont oubliées)
MAX_STORED_MATCHES = 2000
# Durée fictive d'une partie normale (secondes) et d'un remake
NORMAL_DURATION_RANGE = (900, 1680)
REMAKE_DURATION_S = 180

# Identifiants image Data Dragon (= `championName` Match-V5) et championId, par poste
CHAMPIONS_BY_POSITION: dict[str, list[tuple[str, int]]] = {
    "TOP": [
        ("Aatrox", 266), ("Camille", 164), ("Darius", 122), ("Fiora", 114), ("Garen", 86),
        ("Irelia", 39), ("Malphite", 54), ("Renekton", 58), ("Riven", 92), ("Sett", 875),
    ],
    "JUNGLE": [
        ("Graves", 104), ("Khazix", 121), ("LeeSin", 64), ("Viego", 234), ("Vi", 254),
        ("JarvanIV", 59), ("Ekko", 245), ("Diana", 131), ("MonkeyKing", 62),
    ],
    "MIDDLE": [
        ("Ahri", 103), ("Akali", 84), ("Orianna", 61), ("Syndra", 134), ("Zed", 238),
        ("Yasuo", 157), ("Yone", 777), ("Katarina", 55), ("Sylas", 517), ("AurelionSol", 136),
        ("Lux", 99),
    ],
    "BOTTOM": [
        ("Jinx", 222), ("Kaisa", 145), ("Caitlyn", 51), ("Ezreal", 81), ("Vayne", 67),
        ("Ashe", 22), ("Jhin", 202), ("Lucian", 236), ("MissFortune", 21), ("Xayah", 498),
        ("Zeri", 221),
    ],
    "UTILITY": [
        ("Thresh", 412), ("Nautilus", 111), ("Leona", 89), ("Lulu", 117), ("Janna", 40),
        ("Nami", 267), ("Pyke", 555), ("Rakan", 497), ("Senna", 235),
    ],
}
CHAMPION_POOL: list[tuple[str, int]] = [c for pool in CHAMPIONS_BY_POSITION.values() for c in pool]

# Objets plausibles (ids Data Dragon) : 6 objets tirés dans ce pool + bibelot en `item6`
ITEM_POOL: list[int] = [
    3031, 3006, 3072, 3036, 3094, 3046, 3047, 3157, 3089, 3020, 3135, 6653, 3065, 3068, 3110, 3190,
    3107, 3222, 3504, 3153, 3142, 6672, 6673, 6692, 3078, 3100, 3115, 3124, 3165, 3742, 4005, 6662,
]
TRINKETS: list[int] = [3340, 3363, 3364]
# Sorts d'invocateur : Flash (4) + un second sort selon le poste (ids Match-V5)
FLASH_SPELL_ID = 4
SECOND_SPELLS_BY_POSITION: dict[str, list[int]] = {
    "TOP": [12, 14],  # Téléportation, Embrasement
    "JUNGLE": [11],  # Châtiment
    "MIDDLE": [14, 12],
    "BOTTOM": [7, 3],  # Soin, Épuisement
    "UTILITY": [14, 3, 7],
}

BOT_NAMES = [
    "xXDariusMainXx", "Baguette Volante", "JeanMichelGank", "FlashSurF", "Croissant Furtif",
    "LaVieEnRoze", "MidOrAFK", "TontonYasuo", "Pépito", "CamembertFlash", "Zinedine Zed",
    "RaclettePower", "SupportDeLuxe", "Kévin du 93", "Mémé Thresh", "LeBronJinx",
    "Pastis51", "Chocolatine", "Vindieu", "Tartiflette", "ADC en carton", "Nounours",
    "Marcel Pagnol", "Gégé la Rage", "Jacquouille", "TimeoLePro", "Mamie Caitlyn",
]


@dataclass
class _LiveGame:
    game_id: int
    started_at: float  # time.time()
    ends_at: float
    champion: str
    champion_id: int
    position: str
    team_id: int
    ticks: int = 0  # ticks écoulés depuis le démarrage


@dataclass
class _StoredMatch:
    match_id: str
    queue_id: int
    start_ts: float  # secondes epoch (début fictif)
    end_ts: float


@dataclass
class _DemoPlayer:
    puuid: str
    game_name: str
    tag_line: str
    profile_icon_id: int
    summoner_level: int
    tier: str | None
    rank: str | None
    lp: int
    wins: int
    losses: int
    unranked: bool = False
    win_streak: int = 0
    live: _LiveGame | None = None
    cooldown: bool = False  # un tick de pause après une partie
    matches: list[_StoredMatch] = field(default_factory=list)


def demo_puuid(game_name: str, tag_line: str) -> str:
    """puuid déterministe d'un Riot ID."""
    digest = hashlib.sha1(f"{game_name}#{tag_line}".encode("utf-8")).hexdigest()
    return "demo-" + digest[:24]


class DemoRiotClient:
    """Client Riot simulé (voir docstring du module)."""

    seed_names: list[tuple[str, str]] = [
        ("Mike", "MikeLaMalice#DEMO"),
        ("Léa", "Lealicious#EUW"),
        ("Tom", "TomTomLaBombe#FR1"),
        ("Sarah", "SarahCroche#OUI"),
        ("Hugo", "HugoBossDuTop#LOL"),
        ("Chloé", "Chlorophylle#GG1"),
        ("Nico", "NicoTineFree#EUW"),
        ("Manon", "ManonDesSources#TOP"),
    ]

    def __init__(
        self,
        start_chance: float = 0.25,
        game_duration_range: tuple[float, float] = (45, 90),
        rng: random.Random | None = None,
        *,
        remake_chance: float = 0.05,
        win_chance: float = 0.52,
    ):
        self.start_chance = start_chance
        self.game_duration_range = game_duration_range
        self.rng = rng or random.Random()
        self.remake_chance = remake_chance
        self.win_chance = win_chance
        self.request_count = 0
        self._players: dict[str, _DemoPlayer] = {}
        self._matches: dict[str, dict[str, Any]] = {}
        self._game_seq = 0  # identifiant de partie global (DEMO_000001…)

    # ------------------------------------------------------------------ joueurs

    def _ensure_player(self, puuid: str, game_name: str | None = None, tag_line: str | None = None) -> _DemoPlayer:
        player = self._players.get(puuid)
        if player is None:
            seed = random.Random(puuid)  # rang initial déterministe par puuid
            tier, rank, lp = rank_from_absolute_lp(seed.randint(800, 2699))  # Silver IV → Diamond II
            wins = seed.randint(30, 180)
            player = _DemoPlayer(
                puuid=puuid,
                game_name=game_name or f"Joueur{puuid[-4:]}",
                tag_line=tag_line or "DEMO",
                profile_icon_id=seed.randint(0, 28),
                summoner_level=seed.randint(30, 600),
                tier=tier,
                rank=rank,
                lp=lp,
                wins=wins,
                losses=max(0, wins + seed.randint(-30, 30)),
            )
            self._players[puuid] = player
        if game_name is not None:
            player.game_name = game_name
            player.unranked = game_name.strip().lower() == "unranked"
        if tag_line is not None:
            player.tag_line = tag_line
        return player

    # ------------------------------------------------------------------ simulation

    def tick(self, puuid: str | None = None) -> None:
        """Avance la simulation d'un joueur (ou de tous) selon l'horloge réelle."""
        now = time.time()
        if puuid is None:
            players = list(self._players.values())
        else:
            player = self._players.get(puuid)
            players = [player] if player is not None else []
        for player in players:
            self._tick_player(player, now)

    def _tick_player(self, player: _DemoPlayer, now: float) -> None:
        if player.live is not None:
            player.live.ticks += 1
            if now >= player.live.ends_at and player.live.ticks >= MIN_LIVE_TICKS:
                self._finish_game(player, now)
                player.cooldown = True
        elif player.cooldown:
            player.cooldown = False
        elif self.rng.random() < self.start_chance:
            self._start_game(player, now)

    def _start_game(self, player: _DemoPlayer, now: float) -> None:
        self._game_seq += 1
        position = self.rng.choice(POSITIONS)
        champion, champion_id = self.rng.choice(CHAMPIONS_BY_POSITION[position])
        low, high = self.game_duration_range
        player.live = _LiveGame(
            game_id=self._game_seq,
            started_at=now,
            ends_at=now + self.rng.uniform(float(low), float(high)),
            champion=champion,
            champion_id=champion_id,
            position=position,
            team_id=self.rng.choice((100, 200)),
        )

    def _finish_game(self, player: _DemoPlayer, now: float) -> None:
        live = player.live
        assert live is not None
        player.live = None
        remake = self.rng.random() < self.remake_chance
        win = False if remake else self.rng.random() < self.win_chance

        # Durée fictive plausible, fin = maintenant (instant où le rang change)
        duration = REMAKE_DURATION_S if remake else self.rng.randint(*NORMAL_DURATION_RANGE)
        end_ts = now
        start_ts = end_ts - duration

        match_id = f"DEMO_{live.game_id:06d}"
        self._matches[match_id] = self._build_match(player, live, match_id, win, remake, start_ts, end_ts, duration)
        player.matches.append(_StoredMatch(match_id, RANKED_SOLO_QUEUE_ID, start_ts, end_ts))
        self._apply_result(player, win, remake)
        self._trim_matches()

    def _apply_result(self, player: _DemoPlayer, win: bool, remake: bool) -> None:
        if remake:
            return
        if win:
            player.wins += 1
            player.win_streak += 1
        else:
            player.losses += 1
            player.win_streak = 0
        if player.unranked:
            return
        current = absolute_lp(player.tier, player.rank, player.lp)
        if current is None:
            return
        delta = self.rng.randint(14, 26) * (1 if win else -1)
        player.tier, player.rank, player.lp = rank_from_absolute_lp(current + delta)

    def _trim_matches(self) -> None:
        if len(self._matches) <= MAX_STORED_MATCHES:
            return
        for match_id in sorted(self._matches)[: len(self._matches) - MAX_STORED_MATCHES]:
            del self._matches[match_id]
            for player in self._players.values():
                player.matches = [m for m in player.matches if m.match_id != match_id]

    # ------------------------------------------------------------------ génération Match-V5

    def _build_match(
        self,
        player: _DemoPlayer,
        live: _LiveGame,
        match_id: str,
        win: bool,
        remake: bool,
        start_ts: float,
        end_ts: float,
        duration: int,
    ) -> dict[str, Any]:
        minutes = duration / 60
        winning_team = live.team_id if win else (200 if live.team_id == 100 else 100)
        # Champions des 9 autres participants : un par poste et par équipe, sans doublon
        used = {live.champion}
        participants: list[dict[str, Any]] = []
        bot_names = self.rng.sample(BOT_NAMES, 9)
        bot_index = 0
        for team_id in (100, 200):
            for position in POSITIONS:
                participant_id = len(participants) + 1
                team_win = team_id == winning_team
                if team_id == live.team_id and position == live.position:
                    participants.append(
                        self._participant(
                            participant_id, player.puuid, player.game_name, player.tag_line, live.champion,
                            live.champion_id, team_id, position, team_win, minutes, remake,
                            level=player.summoner_level, icon=player.profile_icon_id,
                        )
                    )
                    continue
                candidates = [c for c in CHAMPIONS_BY_POSITION[position] if c[0] not in used]
                champion, champion_id = self.rng.choice(candidates)
                used.add(champion)
                bot_puuid = f"demo-bot-{live.game_id:06d}-{participant_id}"
                participants.append(
                    self._participant(
                        participant_id, bot_puuid, bot_names[bot_index], "EUW", champion, champion_id,
                        team_id, position, team_win, minutes, remake,
                        level=self.rng.randint(30, 500), icon=self.rng.randint(0, 28),
                    )
                )
                bot_index += 1

        # Cohérence par équipe : un joueur ne participe pas à plus de kills que son équipe n'en a
        # (kill participation ≤ 100 %) ; sans kill d'équipe, aucune assist
        for team_id in (100, 200):
            members = [p for p in participants if p["teamId"] == team_id]
            team_kills = sum(p["kills"] for p in members)
            for member in members:
                member["assists"] = max(0, min(member["assists"], team_kills - member["kills"]))

        def objective(team_win: bool, win_range: tuple[int, int], lose_range: tuple[int, int]) -> dict[str, Any]:
            kills = 0 if remake else self.rng.randint(*(win_range if team_win else lose_range))
            return {"first": team_win and not remake, "kills": kills}

        teams = []
        for team_id in (100, 200):
            members = [p for p in participants if p["teamId"] == team_id]
            team_win = team_id == winning_team
            teams.append(
                {
                    "teamId": team_id,
                    "win": team_win,
                    "bans": [],
                    "objectives": {
                        "champion": {"first": team_win and not remake, "kills": sum(p["kills"] for p in members)},
                        "tower": objective(team_win, (5, 11), (0, 6)),
                        "dragon": objective(team_win, (1, 4), (0, 2)),
                        "baron": objective(team_win, (0, 1), (0, 0)),
                        "inhibitor": objective(team_win, (1, 3), (0, 0)),
                        "riftHerald": objective(team_win, (0, 1), (0, 1)),
                    },
                }
            )

        start_ms = int(start_ts * 1000)
        end_ms = int(end_ts * 1000)
        return {
            "metadata": {
                "dataVersion": "2",
                "matchId": match_id,
                "participants": [p["puuid"] for p in participants],
            },
            "info": {
                "endOfGameResult": "GameComplete",
                "gameCreation": start_ms,
                "gameStartTimestamp": start_ms,
                "gameEndTimestamp": end_ms,
                "gameDuration": duration,
                "gameId": live.game_id,
                "gameMode": "CLASSIC",
                "gameName": f"demo_{live.game_id}",
                "gameType": "MATCHED_GAME",
                "gameVersion": DEMO_GAME_VERSION,
                "mapId": 11,
                "platformId": "EUW1",
                "queueId": RANKED_SOLO_QUEUE_ID,
                "tournamentCode": "",
                "participants": participants,
                "teams": teams,
            },
        }

    def _participant(
        self,
        participant_id: int,
        puuid: str,
        game_name: str,
        tag_line: str,
        champion: str,
        champion_id: int,
        team_id: int,
        position: str,
        win: bool,
        minutes: float,
        remake: bool,
        *,
        level: int,
        icon: int,
    ) -> dict[str, Any]:
        rng = self.rng
        if remake:
            stats = {
                "kills": 0, "deaths": 0, "assists": 0,
                "totalMinionsKilled": rng.randint(0, 25), "neutralMinionsKilled": 0,
                "goldEarned": rng.randint(500, 900), "totalDamageDealtToChampions": rng.randint(0, 1500),
                "visionScore": rng.randint(0, 3), "champLevel": rng.randint(2, 4),
            }
        else:
            base_kills = {"TOP": 4.5, "JUNGLE": 5.5, "MIDDLE": 6.0, "BOTTOM": 6.5, "UTILITY": 1.5}[position]
            base_assists = {"TOP": 5.0, "JUNGLE": 8.0, "MIDDLE": 6.5, "BOTTOM": 6.0, "UTILITY": 12.0}[position]
            cs_per_min = {"TOP": 7.2, "JUNGLE": 1.3, "MIDDLE": 7.8, "BOTTOM": 8.3, "UTILITY": 1.2}[position]
            kills = max(0, round(rng.gauss(base_kills * (1.3 if win else 0.75), 2.4)))
            deaths = max(0, round(rng.gauss(3.8 if win else 6.8, 2.2)))
            assists = max(0, round(rng.gauss(base_assists * (1.2 if win else 0.8), 3.0)))
            cs = max(0, round(minutes * cs_per_min * rng.uniform(0.75, 1.15)))
            neutral = round(minutes * 5.2 * rng.uniform(0.8, 1.1)) if position == "JUNGLE" else rng.randint(0, 12)
            gold = round(500 + minutes * (260 if position == "UTILITY" else 300) + kills * 280 + assists * 110 + cs * 18 + neutral * 22)
            damage = round(minutes * (280 if position == "UTILITY" else 820) * rng.uniform(0.6, 1.4))
            vision = round(minutes * (2.4 if position == "UTILITY" else 0.8) * rng.uniform(0.7, 1.3))
            level = max(6, min(18, 6 + int(minutes / 2.1) + rng.randint(-1, 1)))
            stats = {
                "kills": kills, "deaths": deaths, "assists": assists,
                "totalMinionsKilled": cs, "neutralMinionsKilled": neutral,
                "goldEarned": gold, "totalDamageDealtToChampions": damage,
                "visionScore": vision, "champLevel": level,
            }
        # Objets : 1 à 2 en remake, sinon de 3 à 6 selon la durée ; bibelot en `item6`
        item_count = rng.randint(1, 2) if remake else max(3, min(6, int(minutes / 4.5) + rng.randint(0, 1)))
        items = rng.sample(ITEM_POOL, item_count) + [0] * (6 - item_count)
        item_slots = {f"item{i}": items[i] for i in range(6)}
        item_slots["item6"] = rng.choice(TRINKETS)
        spells = [FLASH_SPELL_ID, rng.choice(SECOND_SPELLS_BY_POSITION[position])]
        rng.shuffle(spells)  # Flash en D ou en F, comme dans la vraie vie
        return {
            "participantId": participant_id,
            "puuid": puuid,
            "riotIdGameName": game_name,
            "riotIdTagline": tag_line,
            "summonerName": game_name,
            "summonerLevel": level,
            "profileIcon": icon,
            "championName": champion,
            "championId": champion_id,
            "teamId": team_id,
            "teamPosition": position,
            "individualPosition": position,
            "win": win,
            "gameEndedInEarlySurrender": remake,
            "teamEarlySurrendered": remake,
            "summoner1Id": spells[0],
            "summoner2Id": spells[1],
            **item_slots,
            **stats,
        }

    # ------------------------------------------------------------------ RiotAPI

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> AccountDTO:
        self.request_count += 1
        player = self._ensure_player(demo_puuid(game_name, tag_line), game_name, tag_line)
        return AccountDTO(puuid=player.puuid, game_name=player.game_name, tag_line=player.tag_line)

    async def get_summoner_by_puuid(self, puuid: str) -> SummonerDTO:
        self.request_count += 1
        player = self._ensure_player(puuid)
        return SummonerDTO(
            puuid=puuid,
            summoner_id=f"demo-sum-{puuid[-12:]}",
            profile_icon_id=player.profile_icon_id,
            summoner_level=player.summoner_level,
        )

    async def get_league_entries_by_puuid(self, puuid: str) -> list[LeagueEntryDTO]:
        self.request_count += 1
        player = self._ensure_player(puuid)
        if player.unranked or player.tier is None:
            return []
        return [
            LeagueEntryDTO(
                queue_type="RANKED_SOLO_5x5",
                tier=player.tier,
                rank=player.rank or "I",
                league_points=player.lp,
                wins=player.wins,
                losses=player.losses,
                hot_streak=player.win_streak >= 3,
            )
        ]

    async def get_match_ids_by_puuid(
        self,
        puuid: str,
        queue_id: int,
        start_time: int | None = None,
        count: int = 20,
        start: int = 0,
    ) -> list[str]:
        self.request_count += 1
        self._ensure_player(puuid)
        self.tick(puuid)
        player = self._players[puuid]
        # `start_time` filtre sur la fin de partie (voir docstring du module)
        matches = [
            m for m in player.matches
            if m.queue_id == queue_id and (start_time is None or m.end_ts >= start_time)
        ]
        matches.sort(key=lambda m: (m.end_ts, m.match_id), reverse=True)  # plus récentes d'abord
        offset = max(0, start)
        return [m.match_id for m in matches[offset : offset + max(0, count)]]

    async def get_match(self, match_id: str) -> dict[str, Any]:
        self.request_count += 1
        match = self._matches.get(match_id)
        if match is None:
            raise RiotNotFound(f"Partie démo inconnue : {match_id}", status=404)
        return match

    async def get_active_game(self, puuid: str) -> ActiveGameDTO | None:
        self.request_count += 1
        self._ensure_player(puuid)
        self.tick(puuid)
        live = self._players[puuid].live
        if live is None:
            return None
        # Comme Spectator-V5 : gameStartTime vaut 0 pendant l'écran de chargement (première vue)
        started_at = live.started_at if live.ticks >= 1 else 0.0
        return ActiveGameDTO(
            game_id=live.game_id,
            game_start=datetime.fromtimestamp(started_at, tz=timezone.utc),
            queue_id=RANKED_SOLO_QUEUE_ID,
            game_mode="CLASSIC",
            champion_id=live.champion_id,
            champion_name=live.champion,
        )

    async def aclose(self) -> None:
        return None
