/* Rangs : helpers partagés (App.rk, aussi utilisés par Duos et le Classement) + page /rankings.
   Page : résumé, podium, classement complet, duos par rang moyen, répartition par palier.
   Rafraîchissement : événements (debounce 1 s) + repli toutes les 60 s. */
(function () {
  'use strict';
  const App = window.App;
  const esc = App.escapeHtml;

  /* ------------------------------------------------------------------ */
  /* Helpers partagés                                                     */
  /* ------------------------------------------------------------------ */
  const TIERS = ['IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD', 'DIAMOND', 'MASTER', 'GRANDMASTER', 'CHALLENGER'];
  const TIER_FR = {
    IRON: 'Fer', BRONZE: 'Bronze', SILVER: 'Argent', GOLD: 'Or', PLATINUM: 'Platine', EMERALD: 'Émeraude',
    DIAMOND: 'Diamant', MASTER: 'Maître', GRANDMASTER: 'Grand Maître', CHALLENGER: 'Challenger', UNRANKED: 'Non classé',
  };
  const TIER_SHORT = {
    IRON: 'I', BRONZE: 'B', SILVER: 'S', GOLD: 'G', PLATINUM: 'P', EMERALD: 'E', DIAMOND: 'D', MASTER: 'M', GRANDMASTER: 'GM', CHALLENGER: 'C',
  };
  // Base des emblèmes CommunityDragon (apprise depuis les URLs de l'API si elle diffère)
  let emblemBase = 'https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/';

  const num = (v) => (v === null || v === undefined || v === '' || typeof v === 'boolean' || isNaN(v) ? null : Number(v));
  const text = (v) => (v === null || v === undefined || v === '' || (typeof v === 'number' && isNaN(v)) ? '—' : String(v));
  const normTier = (tier) => {
    const t = String(tier || '').toUpperCase();
    return TIERS.includes(t) ? t : null;
  };

  function learn(url) {
    const m = /^(.*\/)emblem-[a-z]+\.png(?:\?.*)?$/i.exec(url || '');
    if (m) emblemBase = m[1];
  }

  /* LP absolus → tier (≥ 2800 : Master, base commune des tiers apex). */
  function tierFromAbsolute(v) {
    v = num(v);
    if (v === null) return null;
    if (v >= 2800) return 'MASTER';
    return TIERS[Math.min(6, Math.max(0, Math.floor(v / 400)))];
  }

  function emblemUrl(tier) {
    const t = normTier(tier);
    return t ? `${emblemBase}emblem-${t.toLowerCase()}.png` : null;
  }

  /* Emblème de rang : gemme CSS couleur du tier (toujours présente) + image par-dessus si elle charge. */
  function emblem(opts) {
    opts = opts || {};
    const tier = normTier(opts.tier);
    const src = opts.src || emblemUrl(tier);
    if (opts.src) learn(opts.src);
    const color = opts.color || App.rankColor(tier);
    const title = opts.title !== undefined ? opts.title : (tier ? App.tierName(tier) : 'Non classé');
    const img = src && tier
      ? `<img src="${esc(src)}" alt="" ${opts.eager ? '' : 'loading="lazy"'} onload="this.parentNode.classList.add('is-loaded')" onerror="this.remove()">`
      : '';
    return `<span class="rk-emblem rk-emblem-${opts.size || 'sm'}${tier ? '' : ' is-unranked'}" style="--tier-color:${esc(color)}" title="${esc(title)}"><span class="rk-emblem-fb" aria-hidden="true">${tier ? TIER_SHORT[tier] : '–'}</span>${img}</span>`;
  }

  /* Évolution de place depuis le départ : ▲3 / ▼1 / = / nouveau. */
  function moveHtml(p) {
    if (num(p.position) === null) return '<span class="rk-move is-none" title="Non classé">—</span>';
    const d = num(p.position_delta);
    if (d === null) return '<span class="rk-move is-new" title="Pas de rang classé au départ">nouv.</span>';
    if (d > 0) return `<span class="rk-move is-up" title="${d} place${d > 1 ? 's' : ''} gagnée${d > 1 ? 's' : ''} depuis le départ">▲${d}</span>`;
    if (d < 0) return `<span class="rk-move is-down" title="${-d} place${d < -1 ? 's' : ''} perdue${d < -1 ? 's' : ''} depuis le départ">▼${-d}</span>`;
    return '<span class="rk-move is-same" title="Même place qu’au départ">=</span>';
  }

  function teamChip(name, color) {
    if (!name) return '';
    return `<span class="chip chip-team rk-team-chip" style="--team-color:${esc(color || '#e5b64d')}"><span class="swatch"></span><span class="truncate">${esc(name)}</span></span>`;
  }

  function liveBadge(live, compact) {
    if (!live || typeof live !== 'object') return '';
    const elapsed = num(live.elapsed_s) || 0;
    const start = live.game_start ? Date.parse(live.game_start) : NaN;
    const startMs = !isNaN(start) && start > 0 ? start : Date.now() - elapsed * 1000;
    const champ = live.champion_name ? String(live.champion_name) : '';
    const icon = live.champion_icon_url || champ ? App.champIcon({ name: champ || '?', src: live.champion_icon_url, size: 'xs', title: champ }) : '';
    return `<span class="badge-live rk-live" title="En game${champ ? ` · ${esc(champ)}` : ''}">${icon}<span class="dot"></span>${compact ? 'Live' : 'En game'} <span class="detail tnum" data-elapsed-start="${startMs}">${App.formatDuration(elapsed)}</span></span>`;
  }

  function seasonHtml(p) {
    const w = num(p.season_wins);
    const l = num(p.season_losses);
    if (w === null && l === null) return '<span class="muted">—</span>';
    const wr = num(p.season_winrate);
    return `<span class="rk-season"><span class="lp-pos">${w || 0} V</span> – <span class="lp-neg">${l || 0} D</span></span><span class="rk-sub ${App.wrClass(wr)}">${App.formatPct(wr)}</span>`;
  }

  function tickElapsed() {
    const now = Date.now();
    document.querySelectorAll('[data-elapsed-start]').forEach((el) => {
      const start = parseInt(el.dataset.elapsedStart, 10);
      if (!isNaN(start)) el.textContent = App.formatDuration((now - start) / 1000);
    });
  }

  App.rk = { TIERS, TIER_FR, num, text, normTier, tierFromAbsolute, emblemUrl, emblem, moveHtml, teamChip, liveBadge, seasonHtml, learn };

  /* ------------------------------------------------------------------ */
  /* Page /rankings                                                       */
  /* ------------------------------------------------------------------ */
  if (App.page !== 'rankings') return;
  const $ = (sel) => document.querySelector(sel);
  const els = {
    status: $('#rk-status'),
    updated: $('#rk-updated'),
    count: $('#rk-count'),
    hero: $('#rk-hero'),
    podium: $('#rk-podium'),
    body: $('#rk-body'),
    teams: $('#rk-teams'),
    tiers: $('#rk-tiers'),
  };
  let data = null;
  let lastUpdated = null;
  let loading = false;
  const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const d = await App.api('/api/rankings');
      data = {
        challenge: (d && d.challenge) || {},
        players: Array.isArray(d && d.players) ? d.players : [],
        teams: Array.isArray(d && d.teams) ? d.teams : [],
        tiers: Array.isArray(d && d.tiers) ? d.tiers : [],
        summary: (d && d.summary) || {},
      };
      data.players.forEach((p) => learn(p.rank_emblem_url));
      lastUpdated = Date.now();
      els.updated.classList.remove('is-offline');
      render();
      tickUpdated();
      App.refreshLiveCount();
    } catch (e) {
      els.updated.classList.add('is-offline');
      els.updated.textContent = 'Hors ligne';
      if (!data) {
        els.hero.classList.remove('skeleton');
        els.hero.innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><div class="empty-title">Impossible de charger les rangs</div>${esc(e.message)}<br><button type="button" class="btn mt-sm" onclick="location.reload()">Réessayer</button></div>`;
        els.podium.innerHTML = '';
        els.body.innerHTML = '<tr><td colspan="8" class="muted center">—</td></tr>';
      }
      App.toast(e.message || 'Erreur de chargement', { type: 'error' });
    } finally {
      loading = false;
    }
  }

  function tickUpdated() {
    if (!lastUpdated) return;
    const s = Math.round((Date.now() - lastUpdated) / 1000);
    els.updated.textContent = s < 5 ? "Mis à jour à l'instant" : `Mis à jour il y a ${s < 60 ? s + ' s' : App.timeAgoSeconds(s).replace('il y a ', '')}`;
  }

  function render() {
    const s = data.summary;
    els.status.innerHTML = App.statusChip(data.challenge.status);
    const ranked = num(s.ranked_players) || 0;
    const unranked = num(s.unranked_players) || 0;
    els.count.textContent = data.players.length
      ? `${plural(ranked, 'joueur classé', 'joueurs classés')}${unranked ? ` · ${unranked} non classé${unranked > 1 ? 's' : ''}` : ''}`
      : '';
    renderHero();
    renderPodium();
    renderLadder();
    renderTeams();
    renderTiers();
    tickElapsed();
  }

  const byId = (id) => data.players.find((p) => p.player_id === id) || null;
  const playerHref = (p) => `/player/${encodeURIComponent(p.player_id)}`;

  function renderHero() {
    const s = data.summary;
    els.hero.classList.remove('skeleton');
    els.hero.style.minHeight = '';
    const top = s.highest ? byId(s.highest.player_id) || s.highest : null;
    if (!top) {
      els.hero.style.removeProperty('--tier-color');
      els.hero.innerHTML = `<div class="empty"><div class="empty-icon">🎖️</div><div class="empty-title">Aucun joueur classé pour l'instant</div>${data.players.length ? 'Les rangs apparaissent dès qu’un compte lié a un rang Solo/Duo.' : 'Inscris des joueurs sur la page d’<a href="/">accueil</a>.'}</div>`;
      return;
    }
    const tier = normTier(top.tier);
    els.hero.style.setProperty('--tier-color', top.rank_color || App.rankColor(tier));
    const avgTier = tierFromAbsolute(s.avg_absolute_lp);
    const low = s.lowest ? byId(s.lowest.player_id) || s.lowest : null;
    const lowTier = low ? normTier(low.tier) : null;
    els.hero.innerHTML = `
      <div class="rk-hero-main">
        <div class="rk-hero-emblem">${emblem({ tier, src: top.rank_emblem_url, size: 'xl', eager: true })}</div>
        <div class="rk-hero-text">
          <span class="rk-kicker">👑 Plus haut rang</span>
          <div class="rk-hero-name">${App.avatar({ name: top.display_name, src: top.icon_url, color: top.team_color || top.rank_color, size: '' })}<a href="${playerHref(top)}">${esc(top.display_name)}</a></div>
          <div class="rk-hero-rank rank" style="--rank-color:${esc(top.rank_color || App.rankColor(tier))}">${esc(text(top.rank_label))}</div>
          <div class="rk-hero-sub">${teamChip(top.team_name, top.team_color)}${top.season_wins !== undefined ? `<span>Saison ${seasonHtml(top)}</span>` : ''}${liveBadge(top.live)}</div>
        </div>
      </div>
      <div class="rk-hero-stats">
        <div class="rk-stat"><div class="rk-stat-label">Rang moyen</div><div class="rk-stat-value">${emblem({ tier: avgTier, size: 'sm' })}<span class="rank" style="--rank-color:${esc(App.rankColor(avgTier))}">${esc(text(s.avg_rank_label))}</span></div></div>
        <div class="rk-stat"><div class="rk-stat-label">Joueurs classés</div><div class="rk-stat-value big tnum">${num(s.ranked_players) || 0}</div></div>
        <div class="rk-stat"><div class="rk-stat-label">Non classés</div><div class="rk-stat-value big tnum ${num(s.unranked_players) ? '' : 'muted'}">${num(s.unranked_players) || 0}</div></div>
        <div class="rk-stat"><div class="rk-stat-label">Plus bas rang</div><div class="rk-stat-value">${low ? `${emblem({ tier: lowTier, src: low.rank_emblem_url, size: 'sm' })}<span class="rk-stat-text"><a href="${playerHref(low)}">${esc(low.display_name)}</a><span class="rk-sub">${esc(text(low.rank_label))}</span></span>` : '<span class="muted">—</span>'}</div></div>
      </div>`;
  }

  function renderPodium() {
    const top = data.players.filter((p) => num(p.position) !== null).slice(0, 3);
    if (!top.length) {
      els.podium.innerHTML = '<div class="card empty rk-podium-empty"><div class="empty-icon">🏔️</div><div class="empty-title">Podium vide</div>Personne n’a encore de rang classé.</div>';
      return;
    }
    const medals = ['🥇', '🥈', '🥉'];
    els.podium.innerHTML = top.map((p, i) => {
      const tier = normTier(p.tier);
      const color = p.rank_color || App.rankColor(tier);
      return `<article class="card rk-pod rk-pod-${i + 1} is-link" data-href="${playerHref(p)}" style="--team-color:${esc(p.team_color || '#e5b64d')};--tier-color:${esc(color)}">
        <div class="rk-pod-top"><span class="rk-pod-medal" aria-label="${i + 1}${i ? 'e' : 'er'}">${medals[i]}</span>${moveHtml(p)}</div>
        <div class="rk-pod-emblem">${emblem({ tier, src: p.rank_emblem_url, size: i === 0 ? 'xl' : 'lg', eager: true })}</div>
        <div class="rk-pod-who">${App.avatar({ name: p.display_name, src: p.icon_url, color: p.team_color || color, size: i === 0 ? 'lg' : '' })}<a class="rk-pod-name" href="${playerHref(p)}">${esc(p.display_name)}</a></div>
        <div class="rk-pod-rank rank" style="--rank-color:${esc(color)}">${esc(text(p.rank_label))}</div>
        <div class="rk-pod-meta">${teamChip(p.team_name, p.team_color)}${liveBadge(p.live, true)}</div>
        <div class="rk-pod-foot">
          <div><span class="rk-sub">Saison</span>${seasonHtml(p)}</div>
          <div><span class="rk-sub">Depuis le départ</span>${App.lpHtml(num(p.rank_delta_lp))}</div>
        </div>
      </article>`;
    }).join('');
  }

  function renderLadder() {
    const players = data.players;
    if (!players.length) {
      els.body.innerHTML = '<tr><td colspan="8"><div class="empty" style="border:0"><div class="empty-icon">🎮</div><div class="empty-title">Aucun joueur inscrit</div>Les inscriptions se font sur la page d’<a href="/">accueil</a>.</div></td></tr>';
      return;
    }
    const maxAbs = Math.max(1, ...players.map((p) => num(p.absolute_lp) || 0));
    els.body.innerHTML = players.map((p) => {
      const ranked = num(p.position) !== null;
      const tier = ranked ? normTier(p.tier) : null;
      const color = ranked ? (p.rank_color || App.rankColor(tier)) : App.rankColor(null);
      const abs = num(p.absolute_lp);
      const pct = abs === null ? 0 : Math.max(3, Math.round((abs / maxAbs) * 100));
      const promo = num(p.promotions) || 0;
      const demo = num(p.demotions) || 0;
      const baseline = num(p.baseline_absolute_lp) === null ? 'non classé' : text(p.baseline_rank_label);
      return `<tr class="rk-row is-link${ranked ? '' : ' is-unranked'}${num(p.position) === 1 ? ' is-first' : ''}" data-href="${playerHref(p)}">
        <td class="rk-pos tnum">${ranked ? `<span class="pos-badge pos-${p.position}">${p.position}</span>` : '<span class="pos-badge rk-pos-none">–</span>'}</td>
        <td class="center">${moveHtml(p)}</td>
        <td><div class="cell-player rk-player">${App.avatar({ name: p.display_name, src: p.icon_url, color: p.team_color || color, size: 'sm' })}<div class="rk-player-main"><a href="${playerHref(p)}">${esc(p.display_name)}</a>${p.hot_streak ? '<span title="Série de victoires en cours" aria-label="En feu">🔥</span>' : ''}<div class="rk-player-sub">${p.team_name ? teamChip(p.team_name, p.team_color) : '<span class="muted">Sans duo</span>'}${liveBadge(p.live, true)}</div></div></div></td>
        <td><div class="rk-rank-cell">${emblem({ tier, src: ranked ? p.rank_emblem_url : null, size: 'md', color })}<div class="rk-rank-main"><span class="rank" style="--rank-color:${esc(color)}">${esc(ranked ? text(p.rank_label) : 'Non classé')}</span>${ranked ? `<span class="rk-abs-bar" title="${abs} LP absolus"><span style="width:${pct}%;--bar-color:${esc(color)}"></span></span>` : `<span class="rk-sub">${p.is_linked === false ? 'Compte non lié' : 'Pas de rang Solo/Duo'}</span>`}</div></div></td>
        <td class="num"><div class="rk-prog">${num(p.rank_delta_lp) === null ? '<span class="muted">—</span>' : App.lpHtml(num(p.rank_delta_lp))}<span class="rk-sub" title="Rang au départ">départ : ${esc(baseline)}</span></div></td>
        <td>${p.peak_rank_label ? `<span class="rk-peak">${esc(text(p.peak_rank_label))}</span>` : '<span class="muted">—</span>'}</td>
        <td class="num"><div class="rk-prog">${seasonHtml(p)}</div></td>
        <td class="num">${promo || demo ? `<span class="rk-steps">${promo ? `<span class="lp-pos" title="Promotions">▲${promo}</span>` : ''}${demo ? `<span class="lp-neg" title="Rétrogradations">▼${demo}</span>` : ''}</span>` : '<span class="muted">—</span>'}</td>
      </tr>`;
    }).join('');
  }

  function renderTeams() {
    const teams = data.teams;
    if (!teams.length) {
      els.teams.innerHTML = '<div class="card empty" style="grid-column:1/-1"><div class="empty-icon">🤝</div><div class="empty-title">Aucun duo pour l’instant</div>L’organisateur compose les duos dans <a href="/admin#duos">Admin → Duos</a>.</div>';
      return;
    }
    els.teams.innerHTML = teams.map((t) => {
      const avgTier = tierFromAbsolute(t.avg_absolute_lp);
      const color = t.rank_color || App.rankColor(avgTier);
      const pos = num(t.position);
      const members = (Array.isArray(t.players) ? t.players : []).map(byId).filter(Boolean);
      const top = t.top_player_id !== null && t.top_player_id !== undefined ? byId(t.top_player_id) : null;
      return `<article class="card rk-team is-link" data-href="/duos#duo-${encodeURIComponent(t.team_id)}" style="--team-color:${esc(t.color || '#e5b64d')};--tier-color:${esc(color)}">
        <header class="rk-team-head"><span class="pos-badge ${pos === 1 ? 'pos-1' : ''}">${pos === null ? '–' : pos}</span><span class="swatch"></span><a class="rk-team-name truncate" href="/duos#duo-${encodeURIComponent(t.team_id)}">${esc(t.name || 'Duo')}</a>${App.lpHtml(num(t.lp_net))}</header>
        <div class="rk-team-avg">${emblem({ tier: avgTier, size: 'lg', color })}<div><div class="rk-sub">Rang moyen</div><div class="rank" style="--rank-color:${esc(color)}">${esc(num(t.avg_absolute_lp) === null ? 'Non classé' : text(t.rank_label))}</div>${avgTier ? `<div class="rk-sub">${esc(TIER_FR[avgTier])}</div>` : ''}</div></div>
        <ul class="rk-team-players">${members.length ? members.map((p) => {
          const tier = num(p.position) === null ? null : normTier(p.tier);
          const isTop = top && top.player_id === p.player_id;
          return `<li>${App.avatar({ name: p.display_name, src: p.icon_url, color: t.color, size: 'sm' })}<a class="truncate" href="${playerHref(p)}">${esc(p.display_name)}</a>${isTop ? '<span class="chip chip-gold rk-best" title="Meilleur rang du duo">★</span>' : ''}<span class="rk-team-rank">${emblem({ tier, src: tier ? p.rank_emblem_url : null, size: 'xs' })}<span class="rank" style="--rank-color:${esc(tier ? (p.rank_color || App.rankColor(tier)) : App.rankColor(null))}">${esc(text(p.rank_label))}</span></span></li>`;
        }).join('') : '<li class="muted">Aucun joueur</li>'}</ul>
      </article>`;
    }).join('');
  }

  function renderTiers() {
    const tiers = data.tiers;
    if (!tiers.length) {
      els.tiers.innerHTML = '<div class="empty" style="border:0"><div class="empty-icon">📊</div><div class="empty-title">Pas encore de données</div></div>';
      return;
    }
    const max = Math.max(1, ...tiers.map((t) => num(t.count) || 0));
    els.tiers.innerHTML = tiers.map((t) => {
      const tier = normTier(t.tier);
      const count = num(t.count) || 0;
      const names = Array.isArray(t.players) ? t.players : [];
      return `<div class="rk-tier${tier ? '' : ' is-unranked'}" style="--tier-color:${esc(t.color || App.rankColor(tier))}">
        <div class="rk-tier-label">${emblem({ tier, size: 'sm', color: t.color })}<span>${esc(text(t.label || TIER_FR[tier] || t.tier))}</span></div>
        <div class="rk-tier-bar" role="img" aria-label="${count} joueur${count > 1 ? 's' : ''}"><span style="width:${Math.round((count / max) * 100)}%"></span></div>
        <div class="rk-tier-count tnum">${count}</div>
        <div class="rk-tier-names">${names.map((n) => esc(n)).join(' · ') || '—'}</div>
      </div>`;
    }).join('');
  }

  /* Lignes / cartes cliquables (les liens internes gardent leur comportement). */
  document.addEventListener('click', (e) => {
    const target = e.target.closest('[data-href]');
    if (!target || e.target.closest('a, button')) return;
    if (e.metaKey || e.ctrlKey) { window.open(target.dataset.href, '_blank'); return; }
    location.href = target.dataset.href;
  });

  const refresh = App.debounce(load, 1000);
  App.connectEvents({
    rank_changed: refresh,
    match_recorded: refresh,
    live_start: refresh,
    live_end: refresh,
    teams_changed: refresh,
    team_updated: refresh,
    draw_done: refresh,
    challenge_started: refresh,
    challenge_finished: refresh,
    challenge_reset: refresh,
    player_registered: refresh,
    player_linked: refresh,
  });
  document.addEventListener('pekin:connected', () => { if (data) refresh(); });
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  setInterval(tickElapsed, 1000);
  setInterval(tickUpdated, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });

  load();
})();
