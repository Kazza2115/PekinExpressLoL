# Pékin Express LoL

Un petit site pour organiser un **challenge ranked League of Legends entre 8 amis**, sur un
week-end : la roue tire 4 duos au sort, chacun joue ses parties classées, et le site suit
tout en temps réel grâce à l'API Riot. Le duo qui gagne le plus de LP l'emporte.

| Accueil (inscriptions) | La roue des duos |
|---|---|
| ![Accueil](docs/screenshots/accueil.png) | ![La roue](docs/screenshots/roue.png) |

| Classement en direct | Fiche joueur |
|---|---|
| ![Classement](docs/screenshots/classement.png) | ![Fiche joueur](docs/screenshots/joueur.png) |

*(captures prises en mode démo : comptes et parties simulés)*

---

## Le jeu en deux minutes

- **8 joueurs**, inscrits sur la page d'accueil avec leur Riot ID (`Pseudo#TAG`).
- **La roue** tire les joueurs un par un : deux joueurs à la suite forment un duo → **4 duos**.
- Objectif : **10 games classées par jour et par joueur** (réglable).
- **Le duo gagnant est celui qui a gagné le plus de LP nets**, c'est-à-dire la somme des LP
  gagnés (ou perdus) par ses deux joueurs depuis le début du challenge.
- Tout est suivi automatiquement : rang, parties, KDA, séries, qui est « en game »…

### Comment sont calculés les LP nets ?

Le site prend régulièrement une « photo » du rang de chaque joueur (tier, division, LP).
Chaque rang est converti en une valeur absolue : Iron IV 0 LP = 0, chaque division vaut
100 LP, Master et au-delà = 2800 + LP. Les LP nets d'un joueur sont simplement :

```
LP nets = valeur absolue du rang actuel − valeur absolue du rang au début du challenge
```

Passer de Gold IV 80 LP à Gold III 10 LP fait donc **+30 LP nets**, promotion comprise.
Un joueur non classé (« Unranked ») compte pour 0. Les LP nets d'un duo = somme de ses deux
joueurs. En cas d'égalité, le winrate puis le nombre de parties départagent.

### Ce qui compte, ce qui ne compte pas

- Seules les parties **Ranked Solo/Duo** comptent (la **Flex** peut être ajoutée en option).
- Les **remakes** (parties de moins de 5 minutes) sont **exclus** des statistiques.
- Une partie compte si elle **se termine** entre « Démarrer » et « Terminer » (c'est à la fin
  de la partie que les LP sont attribués). Une partie en cours au moment du clic sur
  « Démarrer » compte donc, et le compteur « 10 games par jour » suit la même règle.

---

## Démarrage rapide en mode démo (sans clé Riot)

Le mode démo simule 8 joueurs et leurs parties : idéal pour découvrir le site ou le tester
avant le week-end. Aucune connexion à Riot n'est nécessaire.

Prérequis : **Python 3.11 ou plus récent**.

```bash
# 1. Récupérer le projet et se placer dedans
cd PekinExpressLoL

# 2. Créer un environnement virtuel et installer les dépendances
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt

# 3. Lancer le serveur
uvicorn app.main:app --reload
```

Puis ouvre **http://localhost:8000**. Sans fichier `.env` (ou avec `RIOT_API_KEY` vide), le
site démarre automatiquement en **MODE DÉMO** (badge dans la barre de navigation).

Sur la page d'accueil, clique sur **« Remplir avec des joueurs démo »** : 8 joueurs fictifs
sont inscrits et liés. Tu peux ensuite aller sur **La roue**, tirer les duos, démarrer le
challenge et regarder le classement bouger tout seul (les parties simulées durent entre 45
et 90 secondes).

Le mot de passe organisateur par défaut est `change-me`.

---

## Passer en réel : brancher l'API Riot

### 1. Obtenir une clé

Va sur [developer.riotgames.com](https://developer.riotgames.com) et connecte-toi avec ton
compte Riot.

- **Clé de développement** : disponible immédiatement sur la page d'accueil du portail,
  mais elle **expire toutes les 24 h**. Il faut la régénérer et la recoller dans `.env`
  chaque jour (voir « Recharger .env » plus bas). Suffisant pour un week-end si quelqu'un
  s'en occupe.
- **Clé personnelle (Personal API Key)** : il faut enregistrer une « application » sur le
  portail (quelques lignes de description, validation par Riot sous quelques jours). Elle
  **n'expire pas** : c'est la solution confortable.

Les deux types de clé ont les mêmes limites de débit (voir plus bas).

### 2. Remplir le fichier `.env`

Copie `.env.example` en `.env` à la racine du projet et complète-le. Chaque variable :

| Variable | Rôle |
|---|---|
| `RIOT_API_KEY` | Ta clé Riot. Vide = mode démo. |
| `RIOT_PLATFORM` | Serveur de jeu des joueurs : `euw1` (défaut), `eun1`, `na1`, `kr`… |
| `RIOT_REGION` | Région de routage correspondante : `europe` (défaut), `americas`, `asia`, `sea`. |
| `DEMO_MODE` | `true` pour forcer la simulation même avec une clé, `false` pour forcer le réel. Vide = automatique (démo si pas de clé). |
| `POLL_INTERVAL_SECONDS` | Secondes entre deux interrogations de l'API Riot (défaut : 90 en réel, 10 en démo ; minimum 3). |
| `TRACK_FLEX` | `true` pour suivre aussi la file Flex (défaut `false`). Modifiable ensuite dans l'admin. |
| `ADMIN_PASSWORD` | Mot de passe de l'organisateur (page admin, roue, démarrage). **À changer** (défaut `change-me`). |
| `DATABASE_URL` | Base SQLite. Défaut : `sqlite:///./data/tracker.db` (fichier dans `data/`). |
| `GAMES_PER_DAY` | Objectif de parties par jour et par joueur (défaut 10). Modifiable ensuite dans l'admin. |
| `MAX_PLAYERS` | Nombre maximal de joueurs inscrits (défaut 8). Au-delà, l'inscription est refusée. |
| `TIMEZONE` | Fuseau pour découper les journées (défaut `Europe/Paris`). |
| `DISCORD_WEBHOOK_URL` | URL d'un webhook Discord pour recevoir les annonces. Vide = désactivé. |
| `BASE_URL` | Adresse publique du site, utilisée dans les messages Discord (défaut `http://localhost:8000`). |

### 3. Appliquer la configuration

Deux possibilités :

- **Relancer** le serveur (`Ctrl+C` puis `uvicorn app.main:app --reload`), ou
- aller sur la page **Admin** et cliquer sur **« Recharger .env »** : la nouvelle clé est
  prise en compte sans redémarrer. C'est aussi comme ça qu'on remplace une clé de
  développement expirée.

Le badge « MODE DÉMO » disparaît quand une clé est active. Si la clé est refusée, les
erreurs apparaissent dans la section « Système » de l'admin (« Clé Riot invalide ou
expirée »).

### Bon à savoir sur l'API Riot

- **Limites de débit** : 20 requêtes par seconde et 100 requêtes par 2 minutes. Le site les
  respecte tout seul (file d'attente, pauses si Riot renvoie un 429). Avec 8 joueurs et un
  cycle toutes les 90 s, on est très loin du plafond.
- **Délai d'apparition d'une partie** : Riot met en général **1 à 3 minutes** après la fin
  d'une partie pour la rendre disponible. Ajoute le délai de polling : une partie apparaît
  donc dans le feed quelques minutes après l'écran de victoire. C'est normal.
- **Le PUUID dépend de l'application Riot** : l'identifiant interne d'un compte est propre
  à la clé/application qui l'a demandé. Si tu changes de type de clé (par exemple d'une clé
  de développement vers une clé personnelle d'une autre application), les comptes déjà liés
  ne seront plus reconnus : il faut **relier les comptes** (bouton « Lier mon compte » sur
  l'accueil, ou réinitialiser le challenge). Ne fais pas ce changement en plein challenge.
- Le « en game » utilise l'API Spectator : un joueur apparaît en partie au cycle de polling
  suivant son lancement (donc jusqu'à 90 s après).

---

## Déroulé d'un challenge

Le challenge passe par 4 états : **inscriptions** → **duos tirés** → **en cours** → **terminé**.

### 1. Inscriptions (page d'accueil `/`)

Chaque joueur entre son pseudo pour le challenge et son **Riot ID** au format `Pseudo#TAG`
(visible dans le client LoL en cliquant sur son nom en haut à droite, ex. `La Peace#CHILL`).
Le site vérifie le compte auprès de Riot et affiche le rang actuel. On peut s'inscrire sans
Riot ID et le lier plus tard avec le bouton **« Lier mon compte »** sur sa carte.

Quand tous les joueurs actifs sont liés et qu'ils sont en nombre pair (au moins 2), le
bouton **« Tirer les duos »** apparaît.

Une fois le challenge démarré, un compte déjà lié ne peut plus être changé par n'importe qui
(sinon l'historique de rang serait remplacé) : seul l'organisateur, avec son mot de passe,
peut corriger un Riot ID. Un joueur inscrit sans compte peut toujours lier le sien.

Astuce : l'organisateur peut pré-inscrire tout le monde dans `players.yaml` (lu une seule
fois, au premier démarrage, si aucun joueur n'existe encore).

### 2. La roue (`/wheel`)

L'organisateur clique sur **« Lancer la roue »** (le **mot de passe organisateur** est
demandé une fois par session de navigateur). La roue tourne, s'arrête sur chaque joueur et
révèle les duos deux par deux. Pas content du tirage ? **« Relancer la roue »**.

Puis **« Démarrer le challenge »** : à partir de cet instant, les parties comptent. Le site
prend immédiatement la « photo » de rang de référence de chaque joueur.

### 3. Le classement (`/dashboard`)

Pendant le challenge, la page classement affiche :

- le **podium des duos** avec leurs LP nets, victoires/défaites, winrate et le compteur
  « aujourd'hui x/10 » de chaque joueur ;
- le **tableau des joueurs**, triable par LP nets, parties, winrate ou KDA ;
- le **graphe d'évolution des LP** (une courbe par joueur, à la couleur de son duo) ;
- le **feed des dernières parties** (champion, KDA, durée, LP gagnés/perdus) ;
- le panneau **« En game »** : qui joue en ce moment, avec quel champion, depuis combien
  de temps.

Chaque joueur a aussi sa fiche (`/player/<id>`) avec ses stats détaillées, son graphe et la
liste de ses parties (lien op.gg).

La page se met à jour toute seule (flux d'événements), avec un rechargement de secours
toutes les 60 s.

### 4. Terminer

Dans la page **Admin**, **« Terminer »** lance un dernier relevé puis fige le classement à
l'instant du clic. Les parties terminées avant ce moment comptent (le site laisse quelques
minutes de marge pour que Riot les remonte) ; celles terminées après ne comptent plus.

### Option : une fenêtre différente par duo

Si les duos ne jouent pas le même week-end, l'organisateur peut définir dans l'admin, pour
chaque duo, une **fenêtre de début et de fin**. Les LP nets et les stats de ce duo sont
alors calculés sur sa propre fenêtre au lieu de celle du challenge. Laisser vide = fenêtre
du challenge.

---

## Notifications

Le principe : **prévenir tout le monde quand un duo lance une partie**, pour aller
l'encourager (ou le troller).

### Dans le navigateur

- Sur chaque page, des **toasts** (petits messages en bas de l'écran) annoncent les
  événements en direct : un joueur lance une partie, une partie est enregistrée, les duos
  sont tirés, le challenge démarre…
- Le bouton **🔔 « Activer les notifications »** (barre de navigation) demande
  l'autorisation d'envoyer des **notifications système** même quand l'onglet n'est pas au
  premier plan. Elles sont envoyées pour : lancement d'une partie, partie enregistrée
  (résultat + LP), tirage des duos, démarrage du challenge.
- Les navigateurs n'autorisent ces notifications que sur **`localhost` ou en HTTPS**. Sur une
  adresse `http://192.168.x.x` du LAN, le bouton n'aura pas d'effet (les toasts, eux,
  marchent toujours).

### Sur Discord

Renseigne `DISCORD_WEBHOOK_URL` (dans Discord : réglages du salon → Intégrations → Webhooks
→ copier l'URL) et clique sur **« Tester Discord »** dans l'admin. Le site poste ensuite :

- 🔴 **Mike** (Duo Rouge) vient de lancer une partie — **Ahri** *(uniquement les parties classées)* ;
- ✅ **Mike** (Duo Rouge) gagne avec **Ahri** · 7/2/9 · +21 LP *(ou ❌ … perd …, hors
  remakes)* ;
- 🚀 l'annonce du démarrage du challenge, avec l'objectif et le lien vers le classement.

---

## La page Admin (`/admin`)

Protégée par le mot de passe `ADMIN_PASSWORD`. Une fois connecté :

| Section | Action | Effet |
|---|---|---|
| Challenge | **Enregistrer** | Modifie le nom, le nombre de games par jour, les dates de début/fin et l'option « Suivre aussi la file Flex ». |
| Challenge | **🚀 Démarrer** | Passe le challenge « en cours » (il faut avoir tiré les duos). |
| Challenge | **🏁 Terminer** | Fige le classement. |
| Challenge | **Réinitialiser** | Supprime duos, photos de rang et parties ; retour aux inscriptions. Case à cocher pour garder ou non les joueurs inscrits. |
| Duos | **Enregistrer** (par duo) | Renomme le duo, change sa couleur, définit sa fenêtre de dates optionnelle. Lien « Relancer la roue → ». |
| Joueurs | interrupteur **Actif** | Désactive un joueur (exclu du suivi et de la roue) sans le supprimer. |
| Joueurs | **Supprimer** | Supprime le joueur, ses photos de rang et ses parties. Définitif. |
| Système | **Forcer un rafraîchissement** | Lance un cycle d'interrogation Riot immédiatement. |
| Système | **Recharger .env** | Relit `.env` (nouvelle clé, mode démo…) sans redémarrer. |
| Système | **Tester Discord** | Envoie un message de test sur le webhook. |
| En-tête | **Se déconnecter** | Oublie le mot de passe dans ce navigateur. |

La section « Système » affiche aussi l'état du dernier cycle : date, durée, nombre de
requêtes Riot, erreurs éventuelles. C'est le premier endroit où regarder quand « ça ne
remonte pas ».

---

## Hébergement

### Option 1 : sur le PC de l'un d'entre vous (LAN)

Le plus simple pour un week-end chez quelqu'un :

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` rend le site accessible aux autres machines du réseau local. Les autres
ouvrent `http://<IP du PC>:8000` (par exemple `http://192.168.1.42:8000`). Le PC doit rester
allumé pendant tout le challenge (c'est lui qui interroge Riot). Pense au pare-feu Windows
si personne n'arrive à se connecter.

Pour que les amis à distance y accèdent aussi, un tunnel type ngrok / Cloudflare Tunnel
devant le port 8000 fait l'affaire (et donne du HTTPS, donc des notifications navigateur).

### Option 2 : un petit VPS

N'importe quel VPS à quelques euros suffit. Schéma classique :

1. Cloner le projet, créer le venv, installer les dépendances, remplir `.env`
   (mettre `BASE_URL` à l'adresse publique).
2. Lancer `uvicorn app.main:app --host 127.0.0.1 --port 8000` comme service (systemd,
   `nohup`, tmux…). Pas de `--reload` en production.
3. Mettre un **reverse proxy** (Caddy ou nginx) devant, avec HTTPS. Le site utilise un flux
   SSE (`/api/events`) qui reste ouvert en permanence : le serveur envoie déjà l'en-tête
   `X-Accel-Buffering: no` pour nginx, mais prévois un `proxy_read_timeout` long (plusieurs
   minutes) sur cette route. Caddy gère ça tout seul.
4. La base est un simple fichier **SQLite dans `data/`** : à sauvegarder si tu y tiens, à
   supprimer pour repartir de zéro.

Une seule instance du serveur doit tourner à la fois (une seule tâche de polling).

---

## Structure du projet

```
PekinExpressLoL/
├── app/
│   ├── main.py            # application FastAPI, démarrage du poller
│   ├── config.py          # lecture de .env (Settings)
│   ├── events.py          # bus d'événements (SSE)
│   ├── state.py           # état mémoire : parties en cours, dernier cycle
│   ├── api/               # routes JSON (/api), admin (/api/admin), pages HTML
│   ├── db/                # modèles SQLModel et session SQLite
│   ├── riot/              # client Riot réel, client démo, Data Dragon
│   ├── services/          # poller, stats, tirage des duos, inscription, Discord
│   ├── templates/         # pages Jinja2 (accueil, roue, classement, fiche, admin)
│   └── static/            # CSS, JS vanilla, Chart.js
├── data/                  # base SQLite (créée au premier lancement)
├── tests/                 # tests pytest
├── players.yaml           # pré-inscription optionnelle des joueurs
├── .env.example           # modèle de configuration
├── requirements.txt
└── ARCHITECTURE.md        # contrat technique entre les modules
```

Points d'entrée utiles : `/health` (état du serveur en JSON), `/api/state`,
`/api/leaderboard`, `/api/feed`, `/api/live`, `/api/events` (flux SSE).

## Tests

```bash
pytest
```

279 tests, sans réseau (le client Riot est simulé), en quelques secondes.

---

## Limites connues du prototype

- **LP par partie = approximation.** Riot ne dit pas combien de LP une partie a rapporté ;
  le site le déduit de la différence entre deux photos de rang. Si deux parties d'un même
  joueur se terminent entre deux cycles, les LP de ces parties restent « — » dans le feed.
  Les **LP nets** (ce qui fait le classement) restent fiables, puisqu'ils comparent le rang
  actuel au rang de départ.
- **Un seul challenge à la fois.** Pour en refaire un, on réinitialise (ou on supprime le
  fichier SQLite).
- **Pas d'authentification des joueurs.** N'importe qui ayant l'URL peut s'inscrire ou lier
  un compte (dans la limite de `MAX_PLAYERS` et d'un garde-fou anti-spam) ; seules les
  actions d'organisateur sont protégées par mot de passe. C'est pensé pour un groupe
  d'amis, pas pour Internet entier.
- **Flex optionnelle, Solo/Duo par défaut.** Le rang affiché, les LP nets et le graphe sont
  ceux de la Solo/Duo. Avec `TRACK_FLEX` / la case de l'admin, les parties Flex sont
  enregistrées et apparaissent dans le feed, mais ne changent pas le classement.
- **EUW en priorité.** Le lien op.gg des fiches joueur pointe vers la version EUW ; les
  autres serveurs fonctionnent pour le suivi via `RIOT_PLATFORM` / `RIOT_REGION`.
- **Icônes de champions et d'avatars** via Data Dragon : sans accès Internet côté serveur,
  les initiales du joueur sont affichées à la place.
