/* Fiche joueur : en-tête (splash, rang, saison, duo), classements parmi les joueurs, statistiques
   détaillées, répartitions, records, duo & partenaire, champions, graphe LP, parties.
   Toute valeur absente (null : aucune partie dans la fenêtre) s'affiche « — ». */
(function () {
  'use strict';
  const App = window.App;
  const { $, api, toast, escapeHtml: esc, avatar } = App;

  const playerId = parseInt(document.body.dataset.playerId || '0', 10);
  const els = {
    profile: $('#profile'),
    avatar: $('#profile-avatar'),
    level: $('#profile-level'),
    emblem: $('#profile-emblem'),
    name: $('#profile-name'),
    badges: $('#profile-badges'),
    riot: $('#profile-riot'),
    chips: $('#profile-chips'),
    rank: $('#profile-rank'),
    season: $('#profile-season'),
    peak: $('#profile-peak'),
    lp: $('#profile-lp'),
    lpSub: $('#profile-lp-sub'),
    ranks: $('#pf-ranks'),
    tiles: $('#tiles'),
    groups: $('#pf-groups'),
    splits: $('#pf-splits'),
    records: $('#pf-records'),
    duoSection: $('#duo-section'),
    duo: $('#pf-duo'),
    champions: $('#champions'),
    championsCount: $('#champions-count'),
    matches: $('#matches'),
    matchesCount: $('#matches-count'),
    chartCanvas: $('#lp-chart'),
    chartEmpty: $('#chart-empty'),
    liveSection: $('#live-section'),
    liveBoard: $('#pf-live-board'),
  };

  let data = null;     // /api/players/{id}
  let points = null;   // /api/players/{id}/lp-history
  let chart = null;    // graphe LP
  let dayChart = null; // barres « parties par jour »
  let hashDone = false;

  /* ------------------------------------------------------------------ */
  /* Formatage tolérant (null / undefined / NaN → « — »)                  */
  /* ------------------------------------------------------------------ */
  const DASH = '—';
  const has = (v) => v !== null && v !== undefined && v !== '' && !(typeof v === 'number' && isNaN(v));
  const isNum = (v) => has(v) && !isNaN(Number(v));
  /* Nombre à `d` décimales fixes, à la française (2,72). */
  const fix = (v, d) => (isNum(v) ? Number(v).toLocaleString('fr-FR', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }) : DASH);
  /* Entier arrondi avec séparateur de milliers. */
  const int = (v) => (isNum(v) ? App.formatNumber(Math.round(Number(v))) : DASH);
  const pct = (v) => (isNum(v) ? App.formatPct(Number(v)) : DASH);
  const signed = (v, d) => {
    if (!isNum(v)) return DASH;
    const n = Number(v);
    const txt = fix(Math.abs(n), d || 0);
    if (Number(txt.replace(',', '.').replace(/\s/g, '')) === 0) return fix(0, d || 0);
    return n > 0 ? `+${txt}` : `−${txt}`;
  };
  const lpSpan = (v, d) => (isNum(v) ? `<span class="lp ${App.lpClass(Number(v))}">${signed(v, d)} LP</span>` : `<span class="muted">${DASH}</span>`);
  const plural = (n, one, many) => `${isNum(n) ? n : 0} ${Number(n) > 1 ? many : one}`;
  const ratio = (a, b) => (isNum(a) && isNum(b) && Number(b) > 0 ? Number(a) / Number(b) : null);
  /* 1229 → « 20 min 29 s » */
  function dur(s) {
    if (!isNum(s)) return DASH;
    s = Math.max(0, Math.round(Number(s)));
    const m = Math.floor(s / 60);
    const r = s % 60;
    if (!m) return `${r} s`;
    return r ? `${m} min ${String(r).padStart(2, '0')} s` : `${m} min`;
  }
  /* 18428 → « 5 h 07 » */
  function longDur(s) {
    if (!isNum(s)) return DASH;
    s = Math.max(0, Math.round(Number(s)));
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    if (!h) return `${m} min`;
    return `${h} h ${String(m).padStart(2, '0')}`;
  }
  const BAR_CLS = { 'wr-good': 'is-good', 'wr-bad': 'is-bad', 'wr-mid': 'is-mid' };
  const barCls = (wr) => BAR_CLS[App.wrClass(isNum(wr) ? Number(wr) : null)] || 'is-none';
  const clampPct = (v) => (isNum(v) ? Math.max(0, Math.min(100, Number(v))) : 0);
  function wrBar(wr, extraCls) {
    return `<span class="pf-bar ${barCls(wr)} ${extraCls || ''}" role="img" aria-label="Winrate ${esc(pct(wr))}"><span style="width:${clampPct(wr)}%"></span></span>`;
  }
  function wl(w, l) {
    return `<span class="lp-pos">${isNum(w) ? w : 0} V</span> – <span class="lp-neg">${isNum(l) ? l : 0} D</span>`;
  }
  const MULTI_LABELS = { 2: 'Double kill', 3: 'Triple kill', 4: 'Quadra kill', 5: 'Pentakill' };
  const multiLabel = (n) => (!isNum(n) ? DASH : Number(n) >= 5 ? 'Pentakill' : MULTI_LABELS[Number(n)] || 'Aucun');
  const MULTI_KINDS = [
    ['double_kills', 'Double', 'is-double'],
    ['triple_kills', 'Triple', 'is-triple'],
    ['quadra_kills', 'Quadra', 'is-quadra'],
    ['penta_kills', 'Penta', 'is-penta'],
  ];
  /* Badges Double / Triple / Quadra / Penta : tous (profil) ou seulement ceux > 0, du plus fort au plus faible (parties). */
  function multiBadges(src, onlyPositive) {
    const kinds = onlyPositive ? MULTI_KINDS.slice().reverse() : MULTI_KINDS;
    return kinds.map(([key, label, cls]) => {
      const n = src ? src[key] : null;
      const positive = isNum(n) && Number(n) > 0;
      if (onlyPositive && !positive) return '';
      const count = onlyPositive ? (Number(n) > 1 ? ` ×${n}` : '') : ` <b>${isNum(n) ? n : DASH}</b>`;
      return `<span class="pf-mk ${cls}${positive ? '' : ' is-zero'}">${label}${count}</span>`;
    }).join('');
  }
  function streakHtml(streak) {
    const m = /^([WL])(\d+)$/.exec(String(streak || ''));
    if (!m) return `<span class="muted">${DASH}</span>`;
    const n = parseInt(m[2], 10);
    return m[1] === 'W'
      ? `<span class="lp-pos">${n} ${n > 1 ? 'victoires' : 'victoire'}</span>`
      : `<span class="lp-neg">${n} ${n > 1 ? 'défaites' : 'défaite'}</span>`;
  }

  /* ------------------------------------------------------------------ */
  /* En-tête                                                              */
  /* ------------------------------------------------------------------ */
  function renderProfile() {
    const p = data.player || {};
    const s = data.stats || {};
    const team = data.team;
    const rankings = data.rankings || {};
    const color = team ? team.color : (s.rank_color || p.rank_color || App.rankColor(s.tier || p.tier));
    const tmp = document.createElement('div');
    tmp.innerHTML = avatar({ name: p.display_name, src: p.icon_url || s.icon_url, color, size: 'xl' });
    const node = tmp.firstElementChild;
    node.id = 'profile-avatar';
    els.avatar.replaceWith(node);
    els.avatar = node;

    const level = isNum(s.summoner_level) ? s.summoner_level : p.summoner_level;
    els.level.hidden = !isNum(level);
    els.level.textContent = isNum(level) ? String(level) : '';

    els.name.textContent = p.display_name || '';
    document.title = `${p.display_name || 'Joueur'} · Pékin Express LoL`;

    let badges = '';
    if (team) badges += `<a class="chip chip-team chip-lg pf-duo-chip" href="/duos#duo-${encodeURIComponent(team.id)}" style="--team-color:${esc(team.color)}" title="Voir le duo"><span class="swatch"></span>${esc(team.name)}</a>`;
    if (p.active === false) badges += '<span class="chip">Inactif</span>';
    if (s.live) badges += App.liveBadgeHtml(s.live, { playerId });
    els.profile.classList.toggle('is-live', !!s.live);
    els.avatar.classList.toggle('is-live', !!s.live);
    if (s.hot_streak) badges += '<span class="chip chip-gold" title="Série de victoires en cours (Riot)">🔥 En feu</span>';
    els.badges.innerHTML = badges;

    els.riot.innerHTML = p.riot_id
      ? `${esc(p.riot_id)}${p.is_linked ? ' <span class="chip chip-green">✓ lié</span>' : ' <span class="chip chip-gold">À lier</span>'}`
      : '<span class="chip chip-gold">Compte non lié</span>';

    const chips = [];
    if (s.top_champion) {
      chips.push(`<span class="chip pf-chip">${App.champIcon({ name: s.top_champion, src: s.top_champion_icon_url, size: 'xs', title: s.top_champion })}Favori : <strong>${esc(s.top_champion)}</strong>${isNum(s.top_champion_games) ? ` · ${plural(s.top_champion_games, 'partie', 'parties')}` : ''}</span>`);
    }
    const posRank = rankings.absolute_lp;
    if (posRank && isNum(posRank.position)) {
      chips.push(`<span class="chip pf-chip${posRank.position === 1 ? ' chip-gold' : ''}">${posRank.position === 1 ? '👑 ' : ''}#${posRank.position} / ${posRank.total} au rang</span>`);
    }
    if (isNum(s.games) && s.games > 0) chips.push(`<span class="chip pf-chip">${plural(s.games, 'partie', 'parties')} · ${pct(s.winrate)}</span>`);
    if (s.last_game_at) chips.push(`<span class="chip pf-chip" title="${esc(App.formatDateTime(s.last_game_at))}">Dernière partie ${esc(App.timeAgo(s.last_game_at))}</span>`);
    els.chips.innerHTML = chips.join('');

    const tier = s.tier || p.tier;
    const rankColor = s.rank_color || p.rank_color || App.rankColor(tier);
    els.rank.style.setProperty('--rank-color', rankColor);
    els.rank.textContent = s.rank_label || p.rank_label || App.formatRank(tier, s.rank || p.rank, s.lp || p.lp);
    els.emblem.innerHTML = App.rankEmblem(s.rank_emblem_url || p.rank_emblem_url, 'xl', App.tierName(tier));
    els.season.innerHTML = isNum(s.season_wins) || isNum(s.season_losses)
      ? `Saison : ${wl(s.season_wins, s.season_losses)} · <span class="${App.wrClass(s.season_winrate)}">${pct(s.season_winrate)}</span>`
      : '';
    els.peak.innerHTML = s.peak_rank_label && s.peak_rank_label !== s.rank_label
      ? `Pic du challenge : <strong>${esc(s.peak_rank_label)}</strong>`
      : (s.peak_rank_label && isNum(s.games) && s.games > 0 ? '<span class="pf-peak-now">★ Au plus haut du challenge</span>' : '');

    // Splash du champion favori (ou du champion en cours) en fond de l'en-tête
    const splash = s.top_champion_splash_url || (s.live && s.live.champion_splash_url) || null;
    const current = els.profile.querySelector('.splash-bg-img');
    if (splash && (!current || current.getAttribute('src') !== splash)) {
      if (current) current.remove();
      els.profile.insertAdjacentHTML('afterbegin', App.splashImg(splash, true));
    } else if (!splash && current) {
      current.remove();
    }
    els.profile.classList.toggle('splash-bg', !!splash);

    const lpNet = isNum(s.lp_net) ? Number(s.lp_net) : 0;
    els.lp.className = `big ${App.lpClass(lpNet)}`;
    els.lp.textContent = App.formatLp(lpNet);
    els.lpSub.innerHTML = isNum(s.lp_per_game) ? `${lpSpan(s.lp_per_game, 1)} / partie` : '';
  }

  /* ------------------------------------------------------------------ */
  /* Classement parmi les joueurs                                         */
  /* ------------------------------------------------------------------ */
  const RANK_METRICS = [
    ['absolute_lp', 'Rang actuel', (v) => App.rankFromAbsolute(v)],
    ['lp_net', 'LP nets', (v) => `${signed(v)} LP`],
    ['winrate', 'Winrate', pct],
    ['avg_kda', 'KDA moyen', (v) => fix(v, 2)],
    ['lp_per_game', 'LP / partie', (v) => `${signed(v, 1)} LP`],
    ['games', 'Parties jouées', int],
    ['avg_kill_participation', 'Participation aux kills', pct],
    ['avg_damage', 'Dégâts / partie', int],
    ['avg_damage_share', 'Part des dégâts', pct],
    ['avg_kills', 'Kills / partie', (v) => fix(v, 1)],
    ['avg_deaths', 'Morts / partie', (v) => fix(v, 1)],
    ['avg_assists', 'Assists / partie', (v) => fix(v, 1)],
    ['avg_cs_per_min', 'CS / min', (v) => fix(v, 1)],
    ['avg_gold_per_min', 'Or / min', int],
    ['avg_vision', 'Score de vision', (v) => fix(v, 1)],
    ['avg_wards_placed', 'Balises / partie', (v) => fix(v, 1)],
    ['best_win_streak', 'Meilleure série', (v) => `${int(v)} V`],
    ['multikills', 'Multikills', int],
    ['penta_kills', 'Pentakills', int],
    ['first_bloods', 'Premiers sangs', int],
    ['dragon_kills', 'Dragons', int],
    ['turret_kills', 'Tourelles', int],
  ];
  // Compteurs : un « #1 » à 0 (tout le monde à égalité) n'a pas de sens → masqué
  const ZERO_HIDDEN = new Set(['games', 'best_win_streak', 'multikills', 'penta_kills', 'first_bloods', 'dragon_kills', 'turret_kills']);

  function renderRanks() {
    const p = data.player || {};
    const rankings = data.rankings || {};
    const chips = RANK_METRICS.map(([key, label, fmt]) => {
      const r = rankings[key];
      if (!r || !isNum(r.position) || !isNum(r.total)) return '';
      if (ZERO_HIDDEN.has(key) && Number(r.value) === 0) return '';
      const pos = Number(r.position);
      const cls = pos === 1 ? 'is-first' : pos <= 3 ? 'is-podium' : pos === Number(r.total) && r.total > 1 ? 'is-last' : '';
      const hint = r.higher_is_better === false ? ' (moins = mieux)' : '';
      return `<div class="pf-rank-chip ${cls}" title="${esc(label)}${hint} : ${pos}e sur ${r.total}">
        <div class="pf-rank-pos">${pos === 1 ? '<span class="pf-crown" aria-hidden="true">👑</span>' : ''}#${pos}<small> / ${r.total}</small></div>
        <div class="pf-rank-lbl">${esc(label)}${r.higher_is_better === false ? ' <span class="muted">↓</span>' : ''}</div>
        <div class="pf-rank-val tnum">${esc(fmt(r.value))}</div>
      </div>`;
    }).filter(Boolean);
    if (!chips.length) {
      const why = p.active === false || !p.is_linked
        ? 'Le classement ne compte que les joueurs actifs dont le compte Riot est lié.'
        : 'Les positions apparaîtront après la première partie classée du challenge.';
      els.ranks.innerHTML = `<div class="empty pf-empty"><div class="empty-icon">🏅</div><div class="empty-title">Pas encore classé</div>${esc(why)}</div>`;
      return;
    }
    els.ranks.innerHTML = chips.join('');
  }

  /* ------------------------------------------------------------------ */
  /* Vue d'ensemble (tuiles)                                              */
  /* ------------------------------------------------------------------ */
  function tile(label, value, sub) {
    return `<div class="card tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ''}</div>`;
  }

  function renderTiles() {
    const s = data.stats || {};
    const limit = s.games_limit_today || s.games_limit || App.gamesPerDay || DASH;
    els.tiles.innerHTML = [
      tile('Parties', int(s.games || 0), `aujourd'hui ${int(s.games_today || 0)}/${limit}${s.games_over_quota ? ` · ${int(s.games_over_quota)} hors quota` : ''}`),
      tile('V – D', wl(s.wins, s.losses)),
      tile('Winrate', `<span class="${App.wrClass(s.winrate)}">${pct(s.winrate)}</span>`),
      tile('Série', `${streakHtml(s.streak)}${s.hot_streak ? ' 🔥' : ''}`, 'en cours'),
      tile('KDA', fix(s.avg_kda, 2), isNum(s.avg_kills) ? `${fix(s.avg_kills, 1)} / ${fix(s.avg_deaths, 1)} / ${fix(s.avg_assists, 1)}` : ''),
      tile('LP / partie', lpSpan(s.lp_per_game, 1), isNum(s.lp_known_games) ? `sur ${plural(s.lp_known_games, 'partie', 'parties')}` : ''),
      tile('CS / min', fix(s.avg_cs_per_min, 1), isNum(s.avg_cs) ? `${int(s.avg_cs)} CS / partie` : ''),
      tile('Vision', fix(s.avg_vision, 1), 'score moyen'),
      tile('Dégâts', int(s.avg_damage), isNum(s.avg_damage_share) ? `${pct(s.avg_damage_share)} de l'équipe` : 'aux champions, moyenne'),
      tile('Champion favori', s.top_champion ? `<span class="flex" style="gap:8px">${App.champIcon({ name: s.top_champion, src: s.top_champion_icon_url, size: 'sm', title: s.top_champion })}<span class="truncate">${esc(s.top_champion)}</span></span>` : DASH, s.top_champion ? `${plural(s.top_champion_games, 'partie', 'parties')}${isNum(s.top_champion_winrate) ? ` · ${pct(s.top_champion_winrate)}` : ''}` : ''),
    ].join('');
  }

  /* ------------------------------------------------------------------ */
  /* Statistiques détaillées (groupes)                                    */
  /* ------------------------------------------------------------------ */
  function stat(label, value, sub, wide) {
    return `<div class="pf-stat${wide ? ' is-wide' : ''}"><div class="pf-stat-label">${esc(label)}</div><div class="pf-stat-value tnum">${value}</div>${sub ? `<div class="pf-stat-sub">${sub}</div>` : ''}</div>`;
  }
  function group(icon, title, items, cls) {
    return `<article class="card pf-group ${cls || ''}"><header class="pf-group-head"><span class="pf-group-icon" aria-hidden="true">${icon}</span><h3>${esc(title)}</h3></header><div class="pf-stat-grid">${items.join('')}</div></article>`;
  }
  function miniBar(v, cls) {
    return `<span class="pf-minibar ${cls || ''}"><span style="width:${clampPct(v)}%"></span></span>`;
  }

  function renderGroups() {
    const s = data.stats || {};
    const games = isNum(s.games) ? Number(s.games) : 0;
    const perGame = (total) => (games > 0 && isNum(total) ? `${fix(Number(total) / games, 1)} / partie` : '');
    const ofGames = (n) => (games > 0 && isNum(n) ? `${pct((Number(n) / games) * 100)} des parties` : '');
    const limit = s.games_limit_today || s.games_limit || App.gamesPerDay;
    const deadShare = ratio(s.avg_time_dead, s.avg_game_duration);

    els.groups.innerHTML = [
      group('⚔️', 'Combat', [
        stat('K / D / A total', `${int(s.kills)} / ${int(s.deaths)} / ${int(s.assists)}`, 'kills / morts / assists'),
        stat('K / D / A moyen', `${fix(s.avg_kills, 1)} / ${fix(s.avg_deaths, 1)} / ${fix(s.avg_assists, 1)}`, 'par partie'),
        stat('KDA moyen', fix(s.avg_kda, 2), '(K + A) / D'),
        stat('Participation aux kills', pct(s.avg_kill_participation), miniBar(s.avg_kill_participation)),
        stat('Multikills', int(s.multikills), `<span class="pf-mk-row">${multiBadges(s, false)}</span>`, true),
        stat('Premiers sangs', int(s.first_bloods), ofGames(s.first_bloods)),
        stat('Plus grande série de kills', int(s.largest_killing_spree), 'sans mourir'),
        stat('Plus gros multikill', esc(multiLabel(s.largest_multi_kill))),
      ], 'is-combat'),
      group('🌾', 'Farm & économie', [
        stat('CS / partie', int(s.avg_cs), 'sbires + monstres'),
        stat('CS / min', fix(s.avg_cs_per_min, 1)),
        stat('Or / partie', int(s.avg_gold)),
        stat('Or / min', int(s.avg_gold_per_min)),
      ]),
      group('💥', 'Dégâts', [
        stat('Dégâts / partie', int(s.avg_damage), 'aux champions'),
        stat('Dégâts / min', int(s.avg_damage_per_min)),
        stat("Part des dégâts de l'équipe", pct(s.avg_damage_share), miniBar(s.avg_damage_share, 'is-red')),
        stat('Dégâts subis / partie', int(s.avg_damage_taken)),
        stat('Soins / partie', int(s.avg_heal)),
      ]),
      group('👁️', 'Vision', [
        stat('Score de vision', fix(s.avg_vision, 1), 'moyenne par partie'),
        stat('Balises posées', fix(s.avg_wards_placed, 1), 'par partie'),
        stat('Balises détruites', fix(s.avg_wards_killed, 1), 'par partie'),
        stat('Balises de contrôle', fix(s.avg_control_wards, 1), 'achetées par partie'),
      ]),
      group('🏰', 'Objectifs', [
        stat('Tourelles détruites', int(s.turret_kills), perGame(s.turret_kills)),
        stat('Dragons', int(s.dragon_kills), perGame(s.dragon_kills)),
        stat('Barons', int(s.baron_kills), perGame(s.baron_kills)),
        stat('Objectifs volés', int(s.objectives_stolen), 'dragons, barons, hérauts'),
      ]),
      group('⏱️', 'Temps', [
        stat('Temps de jeu total', longDur(s.total_time_played), games > 0 ? plural(games, 'partie', 'parties') : ''),
        stat('Durée moyenne', dur(s.avg_game_duration)),
        stat('Partie la plus longue', dur(s.longest_game_s)),
        stat('Partie la plus courte', dur(s.shortest_game_s)),
        stat('Temps mort / partie', dur(s.avg_time_dead), deadShare !== null ? `${pct(deadShare * 100)} de la partie` : ''),
        stat('Contrôle infligé / partie', dur(s.avg_cc_time), 'temps de CC sur les ennemis'),
      ]),
      group('📈', 'LP', [
        stat('LP nets', lpSpan(s.lp_net), s.games_over_quota
          ? `${int(s.games_over_quota)} partie${s.games_over_quota > 1 ? 's' : ''} hors quota non comptée${s.games_over_quota > 1 ? 's' : ''} (${signed(s.lp_over_quota)} LP${s.lp_over_quota_approx ? ' environ' : ''})`
          : 'depuis le début du challenge'),
        stat('LP / partie', lpSpan(s.lp_per_game, 1), isNum(s.lp_known_games) ? `${plural(s.lp_known_games, 'partie', 'parties')} avec LP connus` : ''),
        stat('Gain moyen', lpSpan(s.avg_lp_win, 1), 'par victoire'),
        stat('Perte moyenne', lpSpan(s.avg_lp_loss, 1), 'par défaite'),
        stat('Meilleur gain', lpSpan(s.best_lp_gain), 'sur une partie'),
        stat('Pire perte', lpSpan(s.worst_lp_loss), 'sur une partie'),
        stat('Promotions / relégations', `<span class="lp-pos">${int(s.promotions)} ↑</span> / <span class="lp-neg">${int(s.demotions)} ↓</span>`, 'changements de division'),
        stat('Évolution du rang', lpSpan(s.rank_delta_lp), 'LP absolus'),
        stat('Pic du challenge', esc(s.peak_rank_label || DASH), '', true),
        stat('Plus bas du challenge', esc(s.low_rank_label || DASH), '', true),
      ], 'is-lp'),
      group('🔥', 'Séries', [
        stat('Série en cours', `${streakHtml(s.streak)}${s.hot_streak ? ' 🔥' : ''}`),
        stat('Meilleure série', `<span class="lp-pos">${int(s.best_win_streak)} V</span>`, 'victoires d’affilée'),
        stat('Pire série', `<span class="lp-neg">${int(s.best_loss_streak)} D</span>`, 'défaites d’affilée'),
        stat('Redditions', int(s.surrenders), ofGames(s.surrenders)),
        stat("Parties aujourd'hui", `${int(s.games_today || 0)} / ${isNum(limit) ? limit : DASH}`, `<span class="progress pf-progress ${isNum(limit) && s.games_today >= limit ? 'done' : ''}"><span style="width:${isNum(limit) && limit > 0 ? clampPct(((s.games_today || 0) / limit) * 100) : 0}%"></span></span>`),
      ]),
    ].join('');
  }

  /* ------------------------------------------------------------------ */
  /* Répartitions (poste, côté, durée, moment, jour)                      */
  /* ------------------------------------------------------------------ */
  function splitRow(o) {
    const g = isNum(o.games) ? Number(o.games) : 0;
    const wr = g > 0 ? o.winrate : null;
    return `<div class="pf-split-row${g ? '' : ' is-empty'}">
      <div class="pf-split-label">${o.lead || ''}<span class="truncate">${esc(o.label || DASH)}</span></div>
      ${wrBar(wr)}
      <div class="pf-split-wr ${App.wrClass(isNum(wr) ? Number(wr) : null)}">${pct(wr)}</div>
      <div class="pf-split-meta">${g ? `${plural(g, 'partie', 'parties')} · ${wl(o.wins, o.losses)}` : 'aucune partie'}${o.extra ? ` · ${o.extra}` : ''}</div>
    </div>`;
  }
  function splitCard(title, icon, body, cls) {
    return `<article class="card pf-split ${cls || ''}"><header class="pf-group-head"><span class="pf-group-icon" aria-hidden="true">${icon}</span><h3>${esc(title)}</h3></header>${body}</article>`;
  }
  const noGames = (txt) => `<div class="pf-split-empty">${esc(txt || 'Aucune partie pour le moment.')}</div>`;

  function renderSplits() {
    const s = data.stats || {};
    const byPos = Array.isArray(s.by_position) ? s.by_position : [];
    const byDur = Array.isArray(s.by_duration) ? s.by_duration : [];
    const byHour = Array.isArray(s.by_hour) ? s.by_hour : [];
    const byDay = Array.isArray(s.by_day) ? s.by_day : [];
    const side = (games, wins) => ({ games: isNum(games) ? games : 0, wins: isNum(wins) ? wins : 0, losses: isNum(games) && isNum(wins) ? games - wins : 0 });
    const blue = side(s.games_blue, s.wins_blue);
    const red = side(s.games_red, s.wins_red);

    const posBody = byPos.length
      ? byPos.map((p) => splitRow({
        lead: `<span class="pf-pos-lead">${App.posIcon(p.icon_url, p.position) || '<span class="pos-icon is-broken" data-label="?"></span>'}</span>`,
        label: p.label || App.positionLabel(p.position) || 'Inconnu',
        games: p.games, wins: p.wins, losses: p.losses, winrate: p.winrate,
        extra: isNum(p.avg_kda) ? `KDA ${fix(p.avg_kda, 2)}` : '',
      })).join('')
      : noGames();
    const sideBody = splitRow({ lead: '<span class="pf-side is-blue"></span>', label: 'Côté bleu', games: blue.games, wins: blue.wins, losses: blue.losses, winrate: s.winrate_blue })
      + splitRow({ lead: '<span class="pf-side is-red"></span>', label: 'Côté rouge', games: red.games, wins: red.wins, losses: red.losses, winrate: s.winrate_red });
    const durBody = byDur.length ? byDur.map((d) => splitRow({ lead: '<span class="pf-lead-ico">⏳</span>', label: d.label, games: d.games, wins: d.wins, losses: d.losses, winrate: d.winrate })).join('') : noGames();
    const HOUR_ICONS = ['🌅', '☀️', '🌆', '🌙'];
    const hourBody = byHour.length ? byHour.map((d, i) => splitRow({ lead: `<span class="pf-lead-ico">${HOUR_ICONS[i] || '🕒'}</span>`, label: d.label, games: d.games, wins: d.wins, losses: d.losses, winrate: d.winrate })).join('') : noGames();
    const dayBody = byDay.length
      ? `<div class="pf-day-chart"><canvas id="pf-day-canvas" role="img" aria-label="Parties par jour et quota quotidien"></canvas></div>`
        + byDay.map((d) => {
          const lim = isNum(d.limit) ? d.limit : null;
          const over = lim !== null && d.games > lim;
          return splitRow({
            lead: '<span class="pf-lead-ico">📅</span>', label: d.label || d.day, games: d.games, wins: d.wins, losses: d.losses, winrate: d.winrate,
            extra: `<span class="${over ? 'lp-neg' : ''}" title="Parties jouées / quota du jour">${int(d.games)}/${lim === null ? DASH : lim} quota</span> · ${lpSpan(d.lp_change)}`,
          });
        }).join('')
      : noGames('Aucune journée jouée pour le moment.');

    if (dayChart) { dayChart.destroy(); dayChart = null; }
    els.splits.innerHTML = [
      splitCard('Par poste', '🧭', posBody),
      splitCard('Côté de la carte', '🗺️', sideBody),
      splitCard('Durée de partie', '⏳', durBody),
      splitCard('Moment de la journée', '🕒', hourBody),
      splitCard('Par jour', '📅', dayBody, 'is-wide'),
    ].join('');
    renderDayChart(byDay);
  }

  function renderDayChart(byDay) {
    const canvas = document.getElementById('pf-day-canvas');
    if (!canvas || !byDay.length || typeof window.Chart === 'undefined') {
      if (canvas) canvas.parentElement.hidden = true;
      return;
    }
    const labels = byDay.map((d) => d.label || d.day || '');
    const games = byDay.map((d) => (isNum(d.games) ? Number(d.games) : 0));
    const limits = byDay.map((d) => (isNum(d.limit) ? Number(d.limit) : null));
    const colors = byDay.map((d, i) => (limits[i] !== null && games[i] > limits[i] ? 'rgba(240,90,90,0.75)' : 'rgba(229,182,77,0.8)'));
    dayChart = new window.Chart(canvas.getContext('2d'), {
      data: {
        labels,
        datasets: [
          { type: 'bar', label: 'Parties', data: games, backgroundColor: colors, borderRadius: 6, maxBarThickness: 46, order: 2 },
          { type: 'line', label: 'Quota', data: limits, borderColor: 'rgba(230,233,239,0.55)', borderDash: [5, 4], borderWidth: 1.5, pointRadius: 3, pointBackgroundColor: 'rgba(230,233,239,0.7)', fill: false, stepped: 'middle', order: 1 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: App.reducedMotion ? false : { duration: 300 },
        plugins: {
          legend: { display: true, labels: { color: '#8b93a7', boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            backgroundColor: '#1d2434', titleColor: '#e6e9ef', bodyColor: '#e6e9ef', borderColor: 'rgba(255,255,255,0.12)', borderWidth: 1, padding: 10,
            callbacks: {
              footer: (items) => {
                const d = items.length ? byDay[items[0].dataIndex] : null;
                if (!d) return '';
                return [`${d.wins || 0} V – ${d.losses || 0} D · ${pct(d.winrate)}`, `LP : ${isNum(d.lp_change) ? `${signed(d.lp_change)} LP` : DASH}`];
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, border: { color: 'rgba(255,255,255,0.08)' }, ticks: { color: '#8b93a7', font: { size: 11 } } },
          y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)', drawTicks: false }, border: { color: 'rgba(255,255,255,0.08)' }, ticks: { color: '#8b93a7', precision: 0, padding: 6, font: { size: 11 } } },
        },
      },
    });
  }

  /* ------------------------------------------------------------------ */
  /* Records personnels                                                   */
  /* ------------------------------------------------------------------ */
  const RECORDS = [
    ['best_kda', 'Meilleur KDA', '🎯'],
    ['most_kills', 'Plus de kills', '⚔️'],
    ['most_assists', "Plus d'assists", '🤝'],
    ['most_damage', 'Plus de dégâts', '💥'],
    ['best_cs_per_min', 'Meilleur farm', '🌾'],
    ['most_vision', 'Meilleure vision', '👁️'],
    ['biggest_lp_gain', 'Plus gros gain de LP', '📈'],
    ['longest_game', 'Partie la plus longue', '⏳'],
    ['shortest_game', 'Partie la plus courte', '⚡'],
  ];

  function renderRecords() {
    const recs = (data.stats && data.stats.records) || {};
    const cards = RECORDS.map(([key, title, icon]) => {
      const r = recs[key];
      if (!r || !has(r.value)) return '';
      const champ = r.champion_name || 'Champion';
      const href = r.match_id ? `#match-${esc(r.match_id)}` : '#matches';
      return `<a class="card pf-record ${r.champion_splash_url ? 'splash-bg' : ''} ${r.win ? 'is-win' : 'is-loss'}" href="${href}" title="Voir la partie">
        ${App.splashImg(r.champion_splash_url)}
        <div class="pf-record-top"><span class="pf-record-title"><span aria-hidden="true">${icon}</span> ${esc(title)}</span><span class="wl-pill ${r.win ? 'win' : 'loss'}">${r.win ? 'V' : 'D'}</span></div>
        <div class="pf-record-main">
          ${App.champIcon({ name: champ, src: r.champion_icon_url, size: 'md', title: champ })}
          <div class="pf-record-txt">
            <div class="pf-record-value">${esc(r.label || String(r.value))}</div>
            <div class="pf-record-sub"><span class="truncate">${esc(champ)}</span>${r.position ? App.posIcon(null, r.position) : ''}<span>· ${esc(App.formatDateTime(r.game_end))}</span></div>
          </div>
        </div>
      </a>`;
    }).filter(Boolean);
    els.records.innerHTML = cards.length
      ? cards.join('')
      : '<div class="empty pf-empty"><div class="empty-icon">🏆</div><div class="empty-title">Pas encore de record</div>Les meilleures performances apparaîtront ici dès la première partie classée.</div>';
  }

  /* ------------------------------------------------------------------ */
  /* Duo & partenaire                                                     */
  /* ------------------------------------------------------------------ */
  function renderDuo() {
    const s = data.stats || {};
    const partner = s.partner;
    const ts = data.team_stats;
    const team = data.team;
    if (!partner && !ts) {
      els.duoSection.hidden = true;
      els.duo.innerHTML = '';
      return;
    }
    els.duoSection.hidden = false;
    const color = (ts && ts.color) || (team && team.color) || '#e5b64d';
    const parts = [];
    if (partner) {
      parts.push(`<article class="card pf-partner" style="--team-color:${esc(color)}">
        <div class="pf-card-kicker">Partenaire de duo</div>
        <a class="pf-partner-id" href="/player/${encodeURIComponent(partner.player_id)}">
          ${avatar({ name: partner.display_name, src: partner.icon_url, size: 'lg', color })}
          <span class="pf-partner-name"><span class="strong">${esc(partner.display_name || 'Partenaire')}</span><span class="muted">Voir son profil →</span></span>
        </a>
        ${splitRow({ lead: '<span class="pf-lead-ico">🤝</span>', label: 'Ensemble', games: partner.together_games, wins: partner.together_wins, losses: partner.together_losses, winrate: partner.together_winrate })}
        ${splitRow({ lead: '<span class="pf-lead-ico">👤</span>', label: 'Sans partenaire', games: partner.solo_games, wins: partner.solo_wins, losses: partner.solo_losses, winrate: partner.solo_winrate })}
        <p class="pf-note">« Ensemble » : parties jouées dans la même partie que ${esc(partner.display_name || 'son partenaire')}.</p>
      </article>`);
    }
    if (ts) {
      const stats = [
        ['LP nets', lpSpan(ts.lp_net)],
        ['Winrate', `<span class="${App.wrClass(ts.winrate)}">${pct(ts.winrate)}</span>`],
        ['Parties', int(ts.games)],
        ['KDA moyen', fix(ts.avg_kda, 2)],
        ['LP / partie', lpSpan(ts.lp_per_game, 1)],
        ['Meilleure série', `${int(ts.best_win_streak)} V`],
      ];
      parts.push(`<a class="card pf-duo-card" href="/duos#duo-${encodeURIComponent(ts.team_id)}" style="--team-color:${esc(color)}">
        <div class="pf-card-kicker">Duo</div>
        <div class="pf-duo-name"><span class="swatch"></span><span class="truncate">${esc(ts.name || 'Duo')}</span>${isNum(ts.position) ? `<span class="chip ${ts.position === 1 ? 'chip-gold' : ''}">${ts.position === 1 ? '👑 ' : ''}#${ts.position} des duos</span>` : ''}</div>
        ${ts.rank_label ? `<div class="pf-duo-rank muted">Rang moyen : <span class="rank" style="--rank-color:${esc(ts.rank_color || App.rankColor(null))}">${esc(ts.rank_label)}</span></div>` : ''}
        <div class="pf-duo-stats">${stats.map(([l, v]) => `<div><span class="pf-stat-label">${esc(l)}</span><span class="pf-duo-val tnum">${v}</span></div>`).join('')}</div>
        <span class="pf-duo-link">Voir le duo →</span>
      </a>`);
    }
    els.duo.innerHTML = parts.join('');
  }

  /* ------------------------------------------------------------------ */
  /* Champions                                                            */
  /* ------------------------------------------------------------------ */
  function renderChampions() {
    const s = data.stats || {};
    const list = (Array.isArray(s.champions) ? s.champions : []).slice();
    els.championsCount.textContent = list.length ? `${list.length} ${list.length > 1 ? 'champions' : 'champion'} · fenêtre du challenge` : '';
    if (!list.length) {
      els.champions.innerHTML = '<div class="empty" style="grid-column:1/-1"><div class="empty-icon">🎭</div><div class="empty-title">Pas encore de champion joué</div>Les champions apparaîtront ici dès la première partie classée.</div>';
      return;
    }
    els.champions.innerHTML = list.map((c, i) => {
      const name = c.champion_name || 'Champion';
      const fav = i === 0 && (!s.top_champion || name === s.top_champion);
      const wr = isNum(c.winrate) ? Number(c.winrate) : null;
      const kdaLine = isNum(c.avg_kills) ? `<small>${fix(c.avg_kills, 1)} / ${fix(c.avg_deaths, 1)} / ${fix(c.avg_assists, 1)}</small>` : '';
      return `<article class="card pf-champ ${fav ? 'is-fav' : ''}">
        ${App.loadingArt({ name, src: c.loading_url, iconSrc: c.icon_url, className: 'pf-champ-art', title: name })}
        <div class="pf-champ-body">
          <div class="pf-champ-head"><span class="pf-champ-name">${esc(name)}</span>${fav ? '<span class="cc-fav">Favori</span>' : ''}</div>
          <div class="pf-champ-games">${plural(c.games, 'partie', 'parties')} · ${wl(c.wins, c.losses)}</div>
          <div class="pf-champ-wr"><span class="${App.wrClass(wr)}">${pct(wr)}</span>${wrBar(wr)}</div>
          <dl class="pf-champ-stats">
            <div class="is-wide"><dt>KDA</dt><dd>${fix(c.avg_kda, 2)} ${kdaLine}</dd></div>
            <div><dt>CS/min</dt><dd>${fix(c.avg_cs_per_min, 1)}</dd></div>
            <div><dt>Dégâts</dt><dd>${int(c.avg_damage)}</dd></div>
            <div><dt>KP</dt><dd>${pct(c.avg_kill_participation)}</dd></div>
            <div><dt>LP</dt><dd>${lpSpan(c.lp_change)}</dd></div>
          </dl>
          ${c.last_played ? `<div class="pf-champ-last muted">Joué ${esc(App.timeAgo(c.last_played))}</div>` : ''}
        </div>
      </article>`;
    }).join('');
  }

  /* ------------------------------------------------------------------ */
  /* Graphe LP                                                            */
  /* ------------------------------------------------------------------ */
  function renderChart() {
    const pts = ((points && points.points) || [])
      .map((pt) => ({ x: Date.parse(pt.t), y: pt.absolute_lp, tier: pt.tier, rank: pt.rank, lp: pt.lp }))
      .filter((pt) => !isNaN(pt.x) && pt.y !== null && pt.y !== undefined);
    if (pts.length < 1 || typeof window.Chart === 'undefined') {
      els.chartEmpty.hidden = false;
      els.chartEmpty.innerHTML = typeof window.Chart === 'undefined'
        ? '<div class="empty-title">Graphe indisponible</div>'
        : '<div class="empty-icon">📈</div><div class="empty-title">Pas encore de données</div>Le graphe se remplit au fil des parties classées.';
      els.chartCanvas.parentElement.hidden = true;
      if (chart) { chart.destroy(); chart = null; }
      return;
    }
    els.chartEmpty.hidden = true;
    els.chartCanvas.parentElement.hidden = false;
    const color = (data && data.team && data.team.color) || '#e5b64d';
    const range = pts[pts.length - 1].x - pts[0].x;
    const tickTime = (v) => {
      const d = new Date(v);
      if (range > 2 * 86400000) return d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
      return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    };
    const dataset = {
      label: (data && data.player && data.player.display_name) || 'LP',
      data: pts,
      borderColor: color,
      backgroundColor: color.replace(/^#([0-9a-f]{6})$/i, (m, h) => `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},0.1)`),
      fill: true,
      borderWidth: 2,
      pointRadius: pts.length > 60 ? 0 : 3,
      pointHoverRadius: 5,
      pointBorderColor: '#121722',
      pointBorderWidth: 2,
      tension: 0.15,
    };
    if (chart) {
      chart.data.datasets = [dataset];
      chart.options.scales.x.ticks.callback = tickTime;
      chart.update('none');
      return;
    }
    chart = new window.Chart(els.chartCanvas.getContext('2d'), {
      type: 'line',
      data: { datasets: [dataset] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: App.reducedMotion ? false : { duration: 300 },
        interaction: { mode: 'nearest', axis: 'x', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1d2434', titleColor: '#e6e9ef', bodyColor: '#e6e9ef',
            borderColor: 'rgba(255,255,255,0.12)', borderWidth: 1, padding: 10, displayColors: false,
            callbacks: {
              title: (items) => (items.length ? App.formatDateTime(items[0].parsed.x) : ''),
              label: (item) => {
                const raw = item.raw || {};
                return raw.tier ? App.formatRank(raw.tier, raw.rank, raw.lp) : App.rankFromAbsolute(item.parsed.y);
              },
            },
          },
        },
        scales: {
          x: { type: 'linear', grid: { color: 'rgba(255,255,255,0.05)', drawTicks: false }, border: { color: 'rgba(255,255,255,0.08)' }, ticks: { color: '#8b93a7', maxTicksLimit: 8, maxRotation: 0, autoSkip: true, padding: 8, callback: tickTime, font: { size: 11 } } },
          y: { grid: { color: 'rgba(255,255,255,0.05)', drawTicks: false }, border: { color: 'rgba(255,255,255,0.08)' }, ticks: { color: '#8b93a7', maxTicksLimit: 7, padding: 8, callback: (v) => App.rankFromAbsolute(v), font: { size: 11 } } },
        },
      },
    });
  }

  /* ------------------------------------------------------------------ */
  /* Parties                                                              */
  /* ------------------------------------------------------------------ */
  function matchExtras(m) {
    const tags = [multiBadges(m, true)];
    if (m.first_blood_kill) tags.push('<span class="pf-tag is-fb" title="Premier sang de la partie">🩸 Premier sang</span>');
    if (m.surrendered) tags.push('<span class="pf-tag is-ff" title="Partie terminée par reddition">🏳️ Reddition</span>');
    if (m.over_quota) tags.push('<span class="pf-tag is-ff" title="Au-delà des parties autorisées ce jour-là : ni LP ni stats">⛔ Hors quota, ne compte pas</span>');
    const facts = [];
    if (isNum(m.damage_share)) facts.push(`<span class="pf-m-fact" title="Part des dégâts de l'équipe">${miniBar(m.damage_share, 'is-red')}${pct(m.damage_share)} dégâts</span>`);
    if (isNum(m.gold_per_min)) facts.push(`<span class="pf-m-fact" title="Or par minute">${int(m.gold_per_min)} or/min</span>`);
    if (isNum(m.wards_placed) || isNum(m.wards_killed)) {
      facts.push(`<span class="pf-m-fact" title="Balises posées / détruites${isNum(m.control_wards_bought) ? ' · balises de contrôle achetées' : ''}">👁 ${int(m.wards_placed)} / ${int(m.wards_killed)}${isNum(m.control_wards_bought) && m.control_wards_bought > 0 ? ` · ${int(m.control_wards_bought)} ctrl` : ''}</span>`);
    }
    const obj = [
      [m.turret_kills, 'tour', 'tours'],
      [m.dragon_kills, 'dragon', 'dragons'],
      [m.baron_kills, 'baron', 'barons'],
      [m.objectives_stolen, 'vol', 'vols'],
    ].filter(([n]) => isNum(n) && Number(n) > 0).map(([n, one, many]) => plural(n, one, many));
    if (obj.length) facts.push(`<span class="pf-m-fact" title="Objectifs">🏰 ${esc(obj.join(' · '))}</span>`);
    const tagHtml = tags.join('');
    if (!tagHtml && !facts.length) return '';
    return `<div class="pf-m-extra">${tagHtml}${facts.join('')}</div>`;
  }

  /* Tableau des scores déplié sous une partie (tous les joueurs, alliés et ennemis). */
  const openDetails = new Set(); // parties dépliées (gardées ouvertes quand la liste se rafraîchit)

  async function toggleMatchDetail(btn, forceOpen) {
    const id = btn.dataset.detail;
    const box = els.matches.querySelector(`[data-detail-for="${CSS.escape(id)}"]`);
    if (!box) return;
    const open = forceOpen === undefined ? box.hidden : forceOpen;
    if (open) openDetails.add(id); else openDetails.delete(id);
    box.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent = open ? '📊 Masquer' : '📊 Détails';
    if (!open || box.dataset.loaded) return;
    box.innerHTML = '<div class="sb-loading muted">Chargement…</div>';
    try {
      box.innerHTML = App.scoreboardHtml(await App.loadScoreboard(id), { focusPlayerId: playerId });
      box.dataset.loaded = '1';
    } catch (e) {
      box.innerHTML = `<div class="muted">${esc(e.message || 'Détail indisponible.')}</div>`;
    }
  }

  function renderMatches() {
    const matches = (Array.isArray(data.matches) ? data.matches : []).slice().sort((a, b) => Date.parse(b.game_start) - Date.parse(a.game_start));
    els.matchesCount.textContent = matches.length ? `${matches.length} ${matches.length > 1 ? 'parties' : 'partie'}` : '';
    if (!matches.length) {
      els.matches.innerHTML = '<div class="empty" style="border:0;padding:24px 0"><div class="empty-icon">🕹️</div><div class="empty-title">Pas encore de partie enregistrée</div>Les parties classées jouées pendant le challenge apparaîtront ici.</div>';
      return;
    }
    const n = (v) => (isNum(v) ? v : DASH);
    els.matches.innerHTML = matches.map((m) => {
      const result = m.is_remake ? '<span class="chip">Remake</span>' : `<span class="wl-pill ${m.win ? 'win' : 'loss'}">${m.win ? 'Victoire' : 'Défaite'}</span>`;
      const opgg = m.opgg_url ? `<a href="${esc(m.opgg_url)}" target="_blank" rel="noopener noreferrer">op.gg ↗</a>` : '';
      const kp = isNum(m.kill_participation) ? `<span class="muted">KP ${Math.round(m.kill_participation)} %</span>` : '';
      const cls = m.is_remake ? 'is-remake' : m.win ? 'is-win' : 'is-loss';
      const sub = [
        `${n(m.cs)} CS${isNum(m.cs_per_min) && m.cs_per_min > 0 ? ` (${fix(m.cs_per_min, 1)}/min)` : ''}`,
        isNum(m.vision_score) ? `vision ${m.vision_score}` : '',
        isNum(m.damage_to_champions) && m.damage_to_champions > 0 ? `${int(m.damage_to_champions)} dégâts` : '',
      ].filter(Boolean).join(' · ');
      return `<div class="match-row ${cls}" id="match-${esc(m.match_id || '')}">
        <div class="m-icon">${App.champIcon({ name: m.champion_name || '?', src: m.champion_icon_url, size: 'lg', level: m.champ_level, title: m.champion_name })}</div>
        <div class="m-spells">${App.spellIcons(m.spell_urls)}</div>
        <div class="m-main">
          <div class="m-champ"><span class="name">${esc(m.champion_name || 'Champion')}</span>${m.position ? App.posIcon(m.position_icon_url, m.position) : ''}${m.queue && m.queue !== 'SOLO' ? `<span class="chip">${esc(App.queueLabel(m.queue))}</span>` : ''}</div>
          <div class="m-line">${result}<span class="m-kda"><strong>${n(m.kills)}/${n(m.deaths)}/${n(m.assists)}</strong> <span class="muted">KDA ${fix(m.kda, 2)}</span></span>${kp}</div>
          <div class="m-sub tnum">${sub}</div>
          ${m.is_remake ? '' : matchExtras(m)}
        </div>
        <div class="m-items">${App.itemRow(m.item_urls, { title: 'Objets en fin de partie' })}</div>
        <div class="m-time"><div class="tnum">${App.formatDuration(m.game_duration)}</div><div class="m-sub">${esc(App.formatDateTime(m.game_start))}</div></div>
        <div class="m-right">${m.is_remake ? '<span class="muted">—</span>' : App.lpHtml(m.lp_change)}<span class="m-sub">${esc(App.timeAgo(m.game_start))}</span>${opgg}${m.match_id ? `<button type="button" class="btn btn-sm btn-ghost m-detail-btn" data-detail="${esc(m.match_id)}" aria-expanded="false">📊 Détails</button>` : ''}</div>
      </div>${m.match_id ? `<div class="match-detail" data-detail-for="${esc(m.match_id)}" hidden></div>` : ''}`;
    }).join('');
    // Lien direct vers une partie (#match-…) : la ligne n'existe qu'après le premier rendu
    if (!hashDone && /^#match-/.test(location.hash || '')) {
      hashDone = true;
      const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (target) {
        target.scrollIntoView({ block: 'center' });
        // Lien « Tableau des scores » des messages Discord : le tableau s'ouvre directement
        const id = decodeURIComponent(location.hash.slice('#match-'.length));
        if (id) openDetails.add(id);
      }
    }
    openDetails.forEach((id) => {
      const btn = els.matches.querySelector(`[data-detail="${CSS.escape(id)}"]`);
      if (btn) toggleMatchDetail(btn, true);
    });
  }

  /* ------------------------------------------------------------------ */
  function safe(fn, name) {
    try { fn(); } catch (e) { console.warn(`Fiche joueur : rendu « ${name} » impossible`, e); }
  }

  async function load() {
    if (!playerId) return;
    const results = await Promise.allSettled([
      api(`/api/players/${playerId}`),
      api(`/api/players/${playerId}/lp-history`),
    ]);
    if (results[0].status === 'fulfilled') {
      data = results[0].value || {};
      safe(renderProfile, 'en-tête');
      safe(renderLiveBoard, 'partie en cours');
      safe(renderRanks, 'classements');
      safe(renderTiles, 'tuiles');
      safe(renderGroups, 'statistiques');
      safe(renderSplits, 'répartitions');
      safe(renderRecords, 'records');
      safe(renderDuo, 'duo');
      safe(renderChampions, 'champions');
      safe(renderMatches, 'parties');
    } else {
      toast(results[0].reason.message, { type: 'error' });
      if (!data) {
        els.tiles.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-title">Impossible de charger la fiche</div>${esc(results[0].reason.message)}<br><button type="button" class="btn mt-sm" onclick="location.reload()">Réessayer</button></div>`;
        els.ranks.innerHTML = '';
        els.groups.innerHTML = '';
        els.splits.innerHTML = '';
        els.records.innerHTML = '';
        els.champions.innerHTML = '';
        els.matches.innerHTML = '';
      }
    }
    if (results[1].status === 'fulfilled') points = results[1].value;
    else if (!points && data && Array.isArray(data.snapshots)) {
      // Repli : les snapshots de la fiche si l'historique dédié échoue
      points = { points: data.snapshots.map((s) => ({ t: s.captured_at || s.t, absolute_lp: s.absolute_lp, tier: s.tier, rank: s.rank, lp: s.lp })) };
    }
    safe(renderChart, 'graphe LP');
  }

  /* Partie en cours : le tableau des 10 joueurs sous l'en-tête (état partagé App.live, sinon la
     fiche), redessiné seulement quand la partie change. */
  let liveSig = '';
  function renderLiveBoard() {
    if (!els.liveSection || !els.liveBoard) return;
    const mine = App.live.byPlayer.get(playerId);
    const game = (mine && App.live.byGame.get(String(mine.game_id))) || (App.live.items.length || !data ? null : data.live_game) || null;
    els.liveSection.hidden = !game;
    if (!game) { liveSig = ''; els.liveBoard.innerHTML = ''; return; }
    const sig = JSON.stringify([game.game_id, game.game_start, game.loading, (game.teams || []).map((t) => (t.players || []).map((p) => [p.champion_id, p.rank_label]))]);
    if (sig === liveSig) return;
    liveSig = sig;
    els.liveBoard.innerHTML = `<article class="card lb-card is-live" id="live-game-${esc(game.game_id)}">${App.liveBoardHtml(game, { focusPlayerId: playerId })}</article>`;
  }
  document.addEventListener('pekin:live', () => safe(renderLiveBoard, 'partie en cours'));

  const refresh = App.debounce(() => load().catch((e) => console.warn(e)), 1000);
  App.connectEvents({
    match_recorded: (d) => { if (!d.player_id || d.player_id === playerId) refresh(); },
    rank_changed: (d) => { if (!d.player_id || d.player_id === playerId) refresh(); },
    live_start: (d) => { if (!d.player_id || d.player_id === playerId) refresh(); },
    live_end: (d) => { if (!d.player_id || d.player_id === playerId) refresh(); },
    player_linked: (d) => { if (!d.player_id || d.player_id === playerId) refresh(); },
    challenge_started: refresh,
    challenge_reset: refresh,
    teams_changed: refresh,
    draw_done: refresh,
  });
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
  els.matches.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-detail]');
    if (btn) toggleMatchDetail(btn);
  });
  load();
})();
