# Architecture — Pékin Express LoL

Contrat technique entre les modules. **Tout module doit respecter les signatures
ci‑dessous** : d'autres modules sont écrits en parallèle contre ce document.
Commentaires en français, identifiants en anglais. Python 3.11+, FastAPI, SQLModel,
httpx, Jinja2, JS vanilla, CSS maison (pas de Tailwind), Chart.js vendored.

## Le jeu

- 8 joueurs → 4 duos tirés au sort (« la roue »). Un challenge = une seule ligne `Challenge`.
- Statuts : `registration` (inscriptions + liaison des comptes) → `drawn` (duos tirés) →
  `running` (polling, classement) → `finished`.
- Objectif : 10 games / jour / joueur (`Challenge.games_per_day`). **Le duo gagnant est
  celui qui a gagné le plus de LP nets** (somme des deux joueurs) depuis le début de sa fenêtre
  (`Team.window_start` sinon `Challenge.start_at`).
- LP nets d'un joueur = `absolute_lp(dernier snapshot) − absolute_lp(snapshot de référence)`,
  le snapshot de référence étant le dernier snapshot pris **avant ou au** début de la fenêtre,
  sinon le premier snapshot après. Unranked → `None` (affiché « Unranked », LP nets = 0).
- Les remakes (durée < 5 min, `MatchParticipant.is_remake`) sont exclus des stats.
- « Avertir les joueurs quand un duo lance une partie » : événement `live_start` → SSE
  (toast + Notification navigateur) + webhook Discord optionnel.

## Fichiers déjà écrits (ne pas modifier sans raison)

- `app/config.py` — `Settings` (`get_settings()`, `reload_settings()`). Champs :
  `riot_api_key, riot_platform, riot_region, demo_mode, poll_interval_seconds, track_flex,
  admin_password, database_url, games_per_day, timezone, discord_webhook_url, base_url` ;
  propriétés `tz` (ZoneInfo), `platform_host`, `region_host`, `has_api_key`.
- `app/db/models.py` — `Challenge, Team, Player, RankSnapshot, Match, MatchParticipant`,
  enums `ChallengeStatus`, `Queue`, constantes `QUEUE_IDS`, `QUEUE_TYPES`, `QUEUE_BY_ID`,
  `QUEUE_BY_TYPE`, `REMAKE_MAX_DURATION_S`, helper `utcnow()`.
- `app/db/session.py` — `get_engine(), set_engine(), init_db(), get_session()` (dépendance
  FastAPI), `session_scope()` (context manager), `as_utc(dt)`.
  ⚠️ SQLite renvoie des datetimes **naïfs** : toujours passer par `as_utc()` avant de comparer.
- `app/riot/base.py` — DTOs `AccountDTO, SummonerDTO, LeagueEntryDTO, ActiveGameDTO`,
  protocole `RiotAPI`, exceptions `RiotError, RiotNotFound, RiotUnauthorized, RiotRateLimited`.
- `app/events.py` — `bus: EventBus` (`publish(type, data)`, `subscribe(since_id)`, `recent()`).
- `app/state.py` — `state: AppState` (`live_games: dict[player_id, LiveGameState]`, `last_poll`,
  `poll_count`, `polling`), `LiveGameState`, `PollReport`.
- `app/main.py` — app FastAPI, lifespan (init DB, `ensure_challenge()`, `load_players_yaml()`,
  `Poller(api, bus, state).run_forever()`), inclut `routes_api`, `routes_admin`, `routes_pages`.

## Modules à écrire

### `app/riot/client.py` — client réel

```python
class RiotClient:  # implémente RiotAPI
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None)
    # header X-Riot-Token lu depuis get_settings() À CHAQUE requête (clé rechargeable)
    # rate limiter : token bucket 20 req/1 s + 100 req/2 min par host ; respecte Retry-After sur 429
    # retries : 429 → attendre Retry-After (max 3), 5xx → backoff exponentiel 1,2,4 s (max 3)
    # 404 → RiotNotFound ; 401/403 → RiotUnauthorized ; autres → RiotError(status)
    request_count: int  # compteur total (pour PollReport.requests)
```

### `app/riot/endpoints.py` — wrappers typés (utilisés par `RiotClient`)

Fonctions `parse_account(json) -> AccountDTO`, `parse_summoner(json)`, `parse_league_entries(json)`,
`parse_active_game(json, puuid) -> ActiveGameDTO` (champion du participant dont `puuid` correspond),
URLs : `account_url(region_host, game_name, tag_line)`, etc. (encoder les composants avec
`urllib.parse.quote`).

### `app/riot/ddragon.py` — Data Dragon

```python
async def get_version() -> str                      # cache mémoire 1 h ; fallback "14.1.1" si réseau KO
async def champion_name_from_id(champion_id: int) -> str | None   # champion.json en cache ; None si KO
def profile_icon_url(version: str, icon_id: int | None) -> str | None
def champion_icon_url(version: str, champion_name: str | None) -> str | None
def champion_image_name(champion_name: str) -> str   # "Wukong" → "MonkeyKing", "Kai'Sa" → "Kaisa", "Nunu & Willump" → "Nunu"…
CURRENT_VERSION: str  # dernière version connue (synchrone, pour l'API) ; mise à jour par get_version()
```

### `app/riot/demo.py` — client simulé (`DemoRiotClient`, implémente `RiotAPI`)

- Aucun réseau. Résout **n'importe quel** Riot ID (puuid déterministe = `"demo-" + sha1(riot_id)[:24]`).
  Un rang initial déterministe par puuid (Silver → Diamond, LP aléatoires), icône 0..28.
- Monde simulé : à chaque appel de `get_active_game` / `get_match_ids_by_puuid`, fait avancer la
  simulation (`tick()`) selon l'horloge réelle :
  - un joueur hors partie a une probabilité `start_chance` (défaut 0.25) de **démarrer** une partie
    à chaque tick ; la partie dure `game_duration_range` (défaut 45–90 s en démo) ;
  - à la fin, une partie Match-V5 complète est générée (10 participants, le joueur avec son champion,
    KDA/CS/or/dégâts/vision plausibles, `teamPosition`, `win`), LP ±(14..26) appliqués au rang
    (gestion promotion/rétrogradation de division via `stats.absolute_lp`/`rank_from_absolute_lp`),
    `wins/losses` incrémentés. ~5 % de remakes (durée 180 s, LP 0).
  - deux joueurs démo peuvent tomber dans la même partie (bonus) — optionnel.
- `get_match_ids_by_puuid` respecte `start_time` et `queue_id`. `get_match` renvoie le dict stocké.
- Attribut `request_count` comme le vrai client. `seed_names: list[tuple[display_name, riot_id]]`
  de 8 joueurs démo (pseudos fun) exposé pour `POST /api/demo/fill`.
- Paramétrable via `DemoRiotClient(start_chance=..., game_duration_range=(a, b), rng=random.Random(seed))`.

### `app/riot/__init__.py`

```python
def get_api() -> RiotAPI   # singleton : DemoRiotClient si settings.demo_mode sinon RiotClient
def reset_api() -> None    # tests
```

### `app/services/stats.py` — calculs purs (testés dans `tests/test_stats.py`)

```python
TIERS = ["IRON","BRONZE","SILVER","GOLD","PLATINUM","EMERALD","DIAMOND","MASTER","GRANDMASTER","CHALLENGER"]
DIVISIONS = ["IV","III","II","I"]
RANK_COLORS: dict[str, str]   # IRON "#8a8a8a", BRONZE "#b07a4a", SILVER "#a9b4c0", GOLD "#e5b64d",
                              # PLATINUM "#4fb8a8", EMERALD "#3fbf7f", DIAMOND "#5aa0ff", MASTER "#b465f0",
                              # GRANDMASTER "#f05a5a", CHALLENGER "#7fe0ff", UNRANKED "#6b7280"

def absolute_lp(tier: str | None, rank: str | None, lp: int) -> int | None
    # Iron IV 0 LP = 0 ; chaque division = 100 ; Master+ = 2800 + LP (Master, GM, Challenger partagent la base)
def rank_from_absolute_lp(value: int) -> tuple[str, str | None, int]   # inverse (Master+ → ("MASTER", None, lp))
def format_rank(tier: str | None, rank: str | None, lp: int) -> str     # "Gold II · 45 LP", "Master · 120 LP", "Unranked"
def rank_color(tier: str | None) -> str

@dataclass
class StreakInfo:
    kind: str | None   # "W" | "L" | None
    length: int
    best_win: int
    best_loss: int
    @property
    def label(self) -> str  # "W3", "L2", "—"
def compute_streak(results: list[bool]) -> StreakInfo   # results dans l'ordre chronologique

def kda(kills: int, deaths: int, assists: int) -> float   # deaths 0 → (k+a)
def winrate(wins: int, losses: int) -> float | None       # 0..100, None si 0 partie
def day_key(dt: datetime, tz) -> str                       # "2026-10-10" dans le fuseau

@dataclass
class PlayerStats:
    player_id: int; display_name: str; riot_id: str | None; is_linked: bool; active: bool
    team_id: int | None; profile_icon_id: int | None; icon_url: str | None
    tier: str | None; rank: str | None; lp: int; rank_label: str; rank_color: str
    absolute_lp: int | None; baseline_absolute_lp: int | None; lp_net: int
    games: int; wins: int; losses: int; winrate: float | None
    games_today: int; games_per_day: dict[str, int]; games_limit: int   # games_limit = Challenge.games_per_day
    streak: str; best_win_streak: int; best_loss_streak: int; hot_streak: bool
    avg_kda: float | None; avg_cs_per_min: float | None; avg_vision: float | None; avg_damage: float | None
    top_champion: str | None; top_champion_games: int; top_champion_winrate: float | None
    live: dict | None      # {"champion_name","champion_icon_url","game_start","elapsed_s","queue_id","game_mode"} ou None
    last_game_at: str | None
    def to_dict(self) -> dict

def compute_player_stats(*, player: Player, snapshots: list[RankSnapshot], participants: list[MatchParticipant],
                         window_start: datetime | None, window_end: datetime | None, games_limit: int,
                         tz, now: datetime, live: LiveGameState | None = None, queue: Queue = Queue.SOLO,
                         ddragon_version: str | None = None) -> PlayerStats
    # snapshots/participants triés par date croissante ; ne garder que ceux de `queue` ; participants
    # hors fenêtre et remakes exclus des stats ; `games_today` = jour courant dans `tz`.

@dataclass
class TeamStats:
    team_id: int; name: str; color: str; slot: int; position: int
    window_start: str | None; window_end: str | None
    lp_net: int; games: int; wins: int; losses: int; winrate: float | None; games_today: int
    live_count: int; players: list[PlayerStats]
    def to_dict(self) -> dict

def compute_team_stats(team: Team, players: list[PlayerStats]) -> TeamStats   # position = 0 (rempli par rank_teams)
def rank_teams(teams: list[TeamStats]) -> list[TeamStats]   # tri lp_net desc, puis winrate desc, puis games desc ; remplit position 1..n
def sort_players(players: list[PlayerStats], key: str = "lp_net") -> list[PlayerStats]   # keys: lp_net|winrate|games|kda
```

### `app/services/draw.py` — tirage des duos

```python
TEAM_PALETTE = [("Duo Rouge","#ef4444"),("Duo Bleu","#3b82f6"),("Duo Vert","#22c55e"),("Duo Or","#f59e0b"),
                ("Duo Violet","#a855f7"),("Duo Rose","#ec4899"),("Duo Cyan","#06b6d4"),("Duo Orange","#f97316")]
def draw_pairs(player_ids: list[int], rng: random.Random | None = None) -> list[tuple[int, int]]
    # mélange uniforme (rng.shuffle) ; nécessite un nombre pair ≥ 2 → ValueError sinon
@dataclass
class DrawResult:
    order: list[int]                  # IDs des joueurs dans l'ordre où la roue les tire (paire 1 = order[0], order[1]…)
    teams: list[dict]                 # [{id, name, color, slot, player_ids: [a, b]}]
def perform_draw(session: Session, *, rng=None) -> DrawResult
    # Pré-conditions : challenge.status in (registration, drawn), joueurs actifs ET liés, nombre pair ≥ 2 ;
    # supprime les anciennes teams, crée les nouvelles (palette dans l'ordre), assigne team_id,
    # status → drawn, commit, bus.publish("draw_done", {...}). Lève ValueError (message FR) sinon.
```

### `app/services/registration.py`

```python
def parse_riot_id(raw: str) -> tuple[str, str]   # "Nom#TAG" → ("Nom","TAG") ; ValueError (message FR) si invalide
async def register_player(session, api: RiotAPI, *, display_name: str, riot_id: str | None) -> Player
    # display_name unique (insensible à la casse) → ValueError ; crée le joueur, puis link_player si riot_id ;
    # bus.publish("player_registered", {...})
async def link_player(session, api: RiotAPI, player: Player, riot_id: str) -> Player
    # Account-V1 → puuid (ValueError si déjà utilisé par un autre joueur), Summoner-V4 → icône/niveau,
    # League-V4 → premier RankSnapshot (SOLO, + FLEX si track_flex). Erreur Riot → player.link_error renseigné
    # (message FR lisible : "Riot ID introuvable", "Clé Riot invalide/expirée", …), puuid inchangé, pas d'exception.
    # Succès → link_error None, linked_at = now, bus.publish("player_linked", {...})
```

### `app/services/bootstrap.py`

```python
def ensure_challenge(session: Session | None = None) -> Challenge   # crée la ligne unique si absente
async def load_players_yaml(path: Path) -> None   # si table Player vide : challenge.name/games_per_day + register_player pour chaque entrée
```

### `app/services/notifications.py` — webhook Discord (optionnel)

```python
async def send_discord(content: str, *, settings=None) -> bool   # no-op (False) si discord_webhook_url vide ; jamais d'exception
def format_live_start(player: Player, team: Team | None, champion: str) -> str   # "🔴 **Mike** (Duo Rouge) vient de lancer une partie — Ahri"
def format_match_recorded(player, team, participant: MatchParticipant, lp_change) -> str
```

### `app/services/poller.py`

```python
class Poller:
    def __init__(self, api: RiotAPI, bus: EventBus, state: AppState, settings: Settings | None = None)
    async def run_forever(self) -> None          # boucle : poll_once() puis sleep(poll_interval_seconds) ; jamais ne crashe (log)
    async def poll_once(self) -> PollReport      # pour tous les joueurs actifs + liés ; state.polling True/False ; state.last_poll
    async def poll_player(self, session, player: Player, challenge: Challenge, report: PollReport) -> None
```
`poll_player` :
1. League-V4 → si (tier, rank, lp, wins, losses) ≠ dernier snapshot de la queue → insérer `RankSnapshot`
   (avec `absolute_lp`), publier `rank_changed`. Unranked → snapshot tier None (une seule fois).
   Si aucun snapshot n'existe encore pour la queue → en insérer un (référence).
2. Match-V5 IDs (queue 420, + 440 si flex) depuis `challenge.start_at` (si running) sinon 20 derniers ;
   pour chaque ID absent de `Match` → `get_match`, insérer `Match` + `MatchParticipant` pour **chaque**
   joueur du challenge présent dans la partie (lookup par puuid). `is_remake` si `gameDuration < 300`.
   `lp_change` = diff `absolute_lp` entre le snapshot juste avant et juste après `game_start + duration`
   si disponibles (sinon None ; sera recalculé au poll suivant par `backfill_lp_changes`). Publier `match_recorded`.
3. Spectator-V5 **une seule fois par joueur par cycle** → `state.live_games` : nouvelle partie → publier
   `live_start` + Discord ; partie disparue → `live_end`. 404 = pas en partie (pas une erreur).
4. Ne jamais lever : chaque erreur est ajoutée à `report.errors` et loggée.
Ordre des appels : League pour tous, puis matches, puis spectator (regroupé pour les rate limits).
Pendant `registration`/`drawn`, on polle quand même (rangs à jour + badge « en game »), mais les
parties ne sont stockées que si le challenge est `running` (ou 20 dernières pour la fiche ? → non : rien).

Cycle démo : `poll_interval_seconds` = 10 s par défaut, donc tout doit rester léger.

### `app/api/deps.py`

```python
def require_admin(x_admin_password: str | None = Header(default=None)) -> None   # 401 si absent/incorrect (secrets.compare_digest)
def get_challenge(session=Depends(get_session)) -> Challenge
```

### `app/api/routes_api.py` (préfixe `/api`) — JSON public

| Route | Réponse |
|---|---|
| `GET /api/state` | `{challenge, players: [PlayerPublic], teams: [TeamPublic], demo_mode, games_per_day, live_count, last_poll}` |
| `GET /api/leaderboard?sort=lp_net` | `{challenge, teams: [TeamStats], players: [PlayerStats], generated_at}` (teams `[]` tant que pas de duos) |
| `GET /api/players/{id}` | `{player: PlayerPublic, team: TeamPublic|null, stats: PlayerStats, matches: [MatchRow], snapshots: [SnapshotRow]}` |
| `GET /api/players/{id}/lp-history` | `{player_id, points: [{t, absolute_lp, tier, rank, lp}]}` |
| `GET /api/lp-history` | `{series: [{player_id, display_name, team_id, color, points: [...]}]}` (fenêtre du challenge) |
| `GET /api/feed?limit=30` | `{items: [MatchRow + display_name, team_id, team_color, ago_s]}` (toutes queues, hors remakes, plus récent d'abord) |
| `GET /api/live` | `{live: [{player_id, display_name, team_id, team_name, team_color, champion_name, champion_icon_url, game_start, elapsed_s, queue_id, game_mode}]}` |
| `GET /api/events?since=<id>` | **SSE** (`text/event-stream`), `event: <type>`, `data: <json>`, `id: <id>` ; ping toutes les 20 s |
| `POST /api/players` | body `{display_name, riot_id?}` → 201 `{player: PlayerPublic}` ; 400 `{detail}` |
| `POST /api/players/{id}/link` | body `{riot_id}` → `{player}` (relier / corriger) |
| `POST /api/demo/fill` | démo uniquement (404 sinon) : inscrit les joueurs manquants de `seed_names` jusqu'à 8 → `{players}` |
| `GET /health` | `{status: "ok", demo_mode, challenge_status, last_poll, live_count}` |

`PlayerPublic` = `{id, display_name, riot_id, game_name, tag_line, is_linked, link_error, active, team_id,
profile_icon_id, icon_url, summoner_level, tier, rank, lp, rank_label, rank_color, created_at, linked_at}`
(rang = dernier snapshot SOLO). `TeamPublic` = `{id, name, color, slot, window_start, window_end, player_ids}`.
`MatchRow` = `{match_id, player_id, queue, game_start, game_duration, champion_name, champion_icon_url,
position, win, kills, deaths, assists, kda, cs, cs_per_min, gold, damage_to_champions, vision_score, lp_change,
is_remake, opgg_url}` (`opgg_url` = `https://www.op.gg/summoners/euw/{game_name}-{tag_line}` encodé).
`challenge` = `{id, name, status, start_at, end_at, games_per_day, track_flex}`.

### `app/api/routes_admin.py` (préfixe `/api/admin`, `Depends(require_admin)` partout)

| Route | Effet |
|---|---|
| `POST /api/admin/login` | vérifie le mot de passe → `{ok: true}` (le front le garde en sessionStorage) |
| `POST /api/admin/draw` | `perform_draw()` → `DrawResult` (400 + message FR si impossible) |
| `POST /api/admin/challenge/start` | body `{start_at?}` (défaut now) : status → running, `start_at`, force `poll_once()`, publie `challenge_started` |
| `POST /api/admin/challenge/finish` | status → finished, `end_at` = now |
| `POST /api/admin/challenge/reset` | body `{keep_players: bool}` : supprime teams/snapshots/matches (+ joueurs si demandé), status → registration |
| `PATCH /api/admin/challenge` | `{name?, games_per_day?, start_at?, end_at?, track_flex?}` |
| `PATCH /api/admin/teams/{id}` | `{name?, color?, window_start?, window_end?}` |
| `PATCH /api/admin/players/{id}` | `{display_name?, active?, team_id?}` |
| `DELETE /api/admin/players/{id}` | supprime le joueur (et ses snapshots/participations) |
| `POST /api/admin/refresh` | force un cycle → `PollReport` |
| `POST /api/admin/reload-settings` | `reload_settings()` + `reset_api()` → `{demo_mode, has_api_key}` |
| `POST /api/admin/test-notification` | envoie un message Discord de test → `{sent: bool}` |

### `app/api/routes_pages.py` — HTML (Jinja2, `app/templates/`)

Routes : `/` (home), `/wheel` (roue), `/dashboard` (classement), `/player/{id}` (fiche), `/admin`.
Chaque page reçoit `request`, `page` (nom), `challenge` (dict), `demo_mode`, `games_per_day`,
`player` (fiche uniquement). Les données vivantes sont chargées en JS via l'API.
`templates = Jinja2Templates(directory=...)` ; 404 HTML simple pour un joueur inconnu.

### Front (`app/templates/`, `app/static/`)

- `base.html` : `<html lang="fr">`, meta viewport, `/static/css/app.css`, nav (Accueil · La roue ·
  Classement · Admin), badge « MODE DÉMO » si `demo_mode`, bloc toasts, `/static/js/app.js` (helpers
  `api()`, `timeAgo()`, `escapeHtml()`, toasts, SSE client `connectEvents()` + Notification API,
  bouton « Activer les notifications »), bloc `{% block scripts %}`.
- `home.html` + `js/home.js` : hero (nom du challenge, statut, compteur « X / 8 joueurs »),
  formulaire d'inscription (pseudo + Riot ID `Nom#TAG`, aide), grille 8 slots (cartes joueur :
  avatar, pseudo, Riot ID, rang coloré, badge « Compte lié » / « À lier » + erreur + bouton « Lier »),
  bouton « Remplir avec des joueurs démo » si démo, CTA « Tirer les duos » (→ `/wheel`) quand ≥ 2
  joueurs liés et nombre pair. Si `drawn`/`running`/`finished` : affiche les duos + CTA « Voir le classement ».
- `wheel.html` + `js/wheel.js` : roue (canvas) avec les pseudos ; bouton « Lancer la roue » → demande le
  mot de passe admin (modal, mémorisé en `sessionStorage`) → `POST /api/admin/draw` → anime la roue qui
  s'arrête successivement sur chaque joueur de `order`, révèle les duos deux par deux (cartes colorées),
  puis boutons « Relancer » et « Démarrer le challenge » (`POST /api/admin/challenge/start`).
- `dashboard.html` + `js/dashboard.js` : podium/cartes des duos (position, couleur, LP nets ± colorés,
  W/L, winrate, « aujourd'hui x/10 » par joueur, badge EN GAME animé), tableau joueurs triable
  (LP nets, winrate, parties, KDA), graphe LP (Chart.js, une ligne par joueur, couleur du duo),
  feed des dernières parties, panneau live. Rafraîchissement : SSE + fallback fetch 60 s.
- `player.html` + `js/player.js` : fiche (stats détaillées, graphe LP, liste des parties avec lien op.gg).
- `admin.html` + `js/admin.js` : mot de passe → actions (démarrer/terminer/reset, dates, games/jour,
  renommer duos/couleurs, désactiver/supprimer joueurs, rafraîchir, recharger .env, test Discord, état du
  dernier poll).
- Design (`css/app.css`) : sombre, épuré, « esport » sobre. Tokens CSS (`--bg`, `--surface`, `--text`,
  `--muted`, `--accent`, couleurs de rang), police système (`Inter, system-ui, …`), cartes à coins
  arrondis, responsive (mobile 16 px de gouttière, pas de scroll horizontal). Avatar : `<img>` DDragon
  avec `onerror` → initiales dans un rond coloré. Textes en français.

## Tests (`tests/`)

- `tests/conftest.py` : moteur SQLite en mémoire (`set_engine`, `StaticPool`), `reset_api()`, client
  `TestClient(app)` avec le lifespan **désactivé** pour le poller (variable d'env `PEKIN_DISABLE_POLLER=1`
  lue dans `main.lifespan` → ne pas lancer la tâche) ; `DEMO_MODE=1`.
- `tests/test_stats.py` : absolute_lp (Iron IV 0 → 0, Gold II 45 → 1445, Master 120 → 2920, unranked →
  None, inverse), compute_streak, winrate, kda, compute_player_stats (fenêtre, remakes, games_today),
  rank_teams (égalités).
- `tests/test_draw.py` : paires uniformes, nombre impair → ValueError, perform_draw sur une DB en mémoire.
- `tests/test_poller.py` : poller + `DemoRiotClient` (rng fixé, start_chance=1, durée (0,0)) → snapshots,
  matches, live_start/live_end publiés.
- `tests/test_api.py` : inscription, liaison, draw via admin (401 sans mot de passe), start, leaderboard,
  feed, live, health.

### Note sur `app/riot/__init__.py` (singleton)

```python
_api: RiotAPI | None = None   # tests/conftest.py injecte directement ici (`riot_pkg._api = demo_api`)
def get_api() -> RiotAPI:  # crée DemoRiotClient() ou RiotClient() si _api est None
def reset_api() -> None:   # _api = None (sans fermer : les tests gèrent)
```
`tests/conftest.py` existe déjà : fixtures `engine`, `session`, `demo_api`
(`DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(42))`),
`client` (TestClient avec lifespan, poller désactivé), `admin_headers`, constante `ADMIN_PASSWORD`.
