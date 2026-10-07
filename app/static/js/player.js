/* Fiche joueur : profil, tuiles de stats, graphe LP, liste des parties. */
(function () {
  'use strict';
  const App = window.App;
  const { $, api, toast, escapeHtml: esc, avatar } = App;

  const playerId = parseInt(document.body.dataset.playerId || '0', 10);
  const els = {
    profile: $('#profile'),
    avatar: $('#profile-avatar'),
    emblem: $('#profile-emblem'),
    champions: $('#champions'),
    championsCount: $('#champions-count'),
    name: $('#profile-name'),
    badges: $('#profile-badges'),
    riot: $('#profile-riot'),
    rank: $('#profile-rank'),
    lp: $('#profile-lp'),
    tiles: $('#tiles'),
    matches: $('#matches'),
    matchesCount: $('#matches-count'),
    chartCanvas: $('#lp-chart'),
    chartEmpty: $('#chart-empty'),
    statsHint: $('#stats-hint'),
  };

  let data = null;   // /api/players/{id}
  let points = null; // /api/players/{id}/lp-history
  let chart = null;

  /* ------------------------------------------------------------------ */
  function renderProfile() {
    const p = data.player || {};
    const s = data.stats || {};
    const team = data.team;
    const color = team ? team.color : (p.rank_color || App.rankColor(p.tier));
    const tmp = document.createElement('div');
    tmp.innerHTML = avatar({ name: p.display_name, src: p.icon_url || s.icon_url, color, size: 'xl' });
    const node = tmp.firstElementChild;
    node.id = 'profile-avatar';
    els.avatar.replaceWith(node);
    els.avatar = node;
    els.name.textContent = p.display_name || '';
    document.title = `${p.display_name} · Pékin Express LoL`;
    let badges = '';
    if (team) badges += `<span class="chip chip-team chip-lg" style="--team-color:${esc(team.color)}"><span class="swatch"></span>${esc(team.name)}</span>`;
    if (p.active === false) badges += '<span class="chip">Inactif</span>';
    if (s.live) badges += `<span class="badge-live"><span class="dot"></span>En game${s.live.champion_name ? ` <span class="detail">· ${esc(s.live.champion_name)}</span>` : ''}</span>`;
    els.badges.innerHTML = badges;
    els.riot.innerHTML = p.riot_id
      ? `${esc(p.riot_id)}${p.is_linked ? ' <span class="chip chip-green">✓ lié</span>' : ' <span class="chip chip-gold">À lier</span>'}`
      : '<span class="chip chip-gold">Compte non lié</span>';
    const rankColor = s.rank_color || p.rank_color || App.rankColor(s.tier || p.tier);
    els.rank.style.setProperty('--rank-color', rankColor);
    els.rank.textContent = s.rank_label || p.rank_label || App.formatRank(s.tier || p.tier, s.rank || p.rank, s.lp || p.lp);
    els.emblem.innerHTML = App.rankEmblem(s.rank_emblem_url || p.rank_emblem_url, 'lg', App.tierName(s.tier || p.tier));
    // Splash du champion favori en fond de l'en-tête (voile dégradé : le texte reste lisible)
    const splash = s.top_champion_splash_url || null;
    const current = els.profile.querySelector('.splash-bg-img');
    if (splash && (!current || current.getAttribute('src') !== splash)) {
      if (current) current.remove();
      els.profile.insertAdjacentHTML('afterbegin', App.splashImg(splash, true));
    } else if (!splash && current) {
      current.remove();
    }
    els.profile.classList.toggle('splash-bg', !!splash);
    const lpNet = s.lp_net === undefined ? 0 : s.lp_net;
    els.lp.className = `big ${App.lpClass(lpNet)}`;
    els.lp.textContent = App.formatLp(lpNet);
  }

  function tile(label, value, sub) {
    return `<div class="card tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ''}</div>`;
  }

  function renderTiles() {
    const s = data.stats || {};
    const num = (v, d) => (v === null || v === undefined ? '—' : Number(v).toFixed(d === undefined ? 1 : d));
    const limit = s.games_limit || App.gamesPerDay;
    const streakCls = s.streak && s.streak.startsWith('W') ? 'lp-pos' : s.streak && s.streak.startsWith('L') ? 'lp-neg' : '';
    els.tiles.innerHTML = [
      tile('Parties', `${s.games || 0}`, `aujourd'hui ${s.games_today || 0}/${limit}`),
      tile('V – D', `<span class="lp-pos">${s.wins || 0}</span> – <span class="lp-neg">${s.losses || 0}</span>`),
      tile('Winrate', App.formatPct(s.winrate)),
      tile('Série', `<span class="${streakCls}">${esc(s.streak || '—')}${s.hot_streak ? ' 🔥' : ''}</span>`, 'en cours'),
      tile('Meilleure série', `${s.best_win_streak || 0} V`, `pire : ${s.best_loss_streak || 0} D`),
      tile('KDA', num(s.avg_kda, 2)),
      tile('CS / min', num(s.avg_cs_per_min, 1)),
      tile('Vision', num(s.avg_vision, 0), 'score moyen'),
      tile('Dégâts', s.avg_damage === null || s.avg_damage === undefined ? '—' : App.formatNumber(Math.round(s.avg_damage)), 'aux champions, moyenne'),
      tile('Champion favori', s.top_champion ? `<span class="flex" style="gap:8px">${App.champIcon({ name: s.top_champion, src: s.top_champion_icon_url, size: 'sm', title: s.top_champion })}<span class="truncate">${esc(s.top_champion)}</span></span>` : '—', s.top_champion ? `${s.top_champion_games} ${s.top_champion_games > 1 ? 'parties' : 'partie'}${s.top_champion_winrate !== null && s.top_champion_winrate !== undefined ? ` · ${App.formatPct(s.top_champion_winrate)}` : ''}` : ''),
    ].join('');
  }

  /* ------------------------------------------------------------------ */
  function renderChampions() {
    const s = data.stats || {};
    const list = (s.champions || []).slice();
    els.championsCount.textContent = list.length ? `${list.length} ${list.length > 1 ? 'champions' : 'champion'} · fenêtre du challenge` : '';
    if (!list.length) {
      els.champions.innerHTML = '<div class="empty" style="grid-column:1/-1"><div class="empty-icon">🎭</div><div class="empty-title">Pas encore de champion joué</div>Les champions apparaîtront ici dès la première partie classée.</div>';
      return;
    }
    const dec = (v, d) => (v === null || v === undefined ? '—' : Number(v).toFixed(d));
    els.champions.innerHTML = list.map((c, i) => {
      const fav = i === 0 && (!s.top_champion || c.champion_name === s.top_champion);
      const wr = c.winrate === null || c.winrate === undefined ? null : Number(c.winrate);
      return `<div class="card champ-card ${fav ? 'is-fav' : ''}">
        ${App.champIcon({ name: c.champion_name, src: c.icon_url, size: 'xl', fav, title: c.champion_name, eager: i < 6 })}
        ${fav ? '<span class="cc-fav">Favori</span>' : ''}
        <div class="cc-name">${esc(c.champion_name)}</div>
        <div class="cc-games">${c.games || 0} ${c.games > 1 ? 'parties' : 'partie'} · <span class="lp-pos">${c.wins || 0} V</span> – <span class="lp-neg">${c.losses || 0} D</span></div>
        <div class="cc-wr ${App.wrClass(wr)}">${wr === null ? '—' : `${Math.round(wr)} %`}<small>winrate</small></div>
        <div class="cc-stats"><span>KDA <strong>${dec(c.avg_kda, 2)}</strong></span><span>CS/min <strong>${dec(c.avg_cs_per_min, 1)}</strong></span></div>
      </div>`;
    }).join('');
  }

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
      label: (data.player && data.player.display_name) || 'LP',
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
  function renderMatches() {
    const matches = (data.matches || []).slice().sort((a, b) => Date.parse(b.game_start) - Date.parse(a.game_start));
    els.matchesCount.textContent = matches.length ? `${matches.length} ${matches.length > 1 ? 'parties' : 'partie'}` : '';
    if (!matches.length) {
      els.matches.innerHTML = '<div class="empty" style="border:0;padding:24px 0"><div class="empty-icon">🕹️</div><div class="empty-title">Pas encore de partie enregistrée</div>Les parties classées jouées pendant le challenge apparaîtront ici.</div>';
      return;
    }
    els.matches.innerHTML = matches.map((m) => {
      const result = m.is_remake ? '<span class="chip">Remake</span>' : `<span class="wl-pill ${m.win ? 'win' : 'loss'}">${m.win ? 'Victoire' : 'Défaite'}</span>`;
      const opgg = m.opgg_url ? `<a href="${esc(m.opgg_url)}" target="_blank" rel="noopener noreferrer">op.gg ↗</a>` : '';
      const kp = m.kill_participation === null || m.kill_participation === undefined ? '' : `<span class="muted">KP ${Math.round(m.kill_participation)} %</span>`;
      const cls = m.is_remake ? 'is-remake' : m.win ? 'is-win' : 'is-loss';
      return `<div class="match-row ${cls}">
        <div class="m-icon">${App.champIcon({ name: m.champion_name || '?', src: m.champion_icon_url, size: 'lg', level: m.champ_level, title: m.champion_name })}</div>
        <div class="m-spells">${App.spellIcons(m.spell_urls)}</div>
        <div class="m-main">
          <div class="m-champ"><span class="name">${esc(m.champion_name || 'Champion')}</span>${m.position ? App.posIcon(m.position_icon_url, m.position) : ''}${m.queue && m.queue !== 'SOLO' ? `<span class="chip">${esc(App.queueLabel(m.queue))}</span>` : ''}</div>
          <div class="m-line">${result}<span class="m-kda"><strong>${m.kills}/${m.deaths}/${m.assists}</strong> <span class="muted">KDA ${m.kda === null || m.kda === undefined ? '—' : Number(m.kda).toFixed(2)}</span></span>${kp}</div>
          <div class="m-sub tnum">${m.cs} CS${m.cs_per_min ? ` (${Number(m.cs_per_min).toFixed(1)}/min)` : ''}${m.vision_score !== null && m.vision_score !== undefined ? ` · vision ${m.vision_score}` : ''}${m.damage_to_champions ? ` · ${App.formatNumber(m.damage_to_champions)} dégâts` : ''}</div>
        </div>
        <div class="m-items">${App.itemRow(m.item_urls, { title: 'Objets en fin de partie' })}</div>
        <div class="m-time"><div class="tnum">${App.formatDuration(m.game_duration)}</div><div class="m-sub">${esc(App.formatDateTime(m.game_start))}</div></div>
        <div class="m-right">${m.is_remake ? '<span class="muted">—</span>' : App.lpHtml(m.lp_change)}<span class="m-sub">${esc(App.timeAgo(m.game_start))}</span>${opgg}</div>
      </div>`;
    }).join('');
  }

  /* ------------------------------------------------------------------ */
  async function load() {
    if (!playerId) return;
    const results = await Promise.allSettled([
      api(`/api/players/${playerId}`),
      api(`/api/players/${playerId}/lp-history`),
    ]);
    if (results[0].status === 'fulfilled') {
      data = results[0].value;
      renderProfile();
      renderTiles();
      renderChampions();
      renderMatches();
    } else {
      toast(results[0].reason.message, { type: 'error' });
      if (!data) {
        els.tiles.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-title">Impossible de charger la fiche</div>${esc(results[0].reason.message)}<br><button type="button" class="btn mt-sm" onclick="location.reload()">Réessayer</button></div>`;
        els.champions.innerHTML = '';
        els.matches.innerHTML = '';
      }
    }
    if (results[1].status === 'fulfilled') points = results[1].value;
    else if (!points && data && Array.isArray(data.snapshots)) {
      // Repli : les snapshots de la fiche si l'historique dédié échoue
      points = { points: data.snapshots.map((s) => ({ t: s.captured_at || s.t, absolute_lp: s.absolute_lp, tier: s.tier, rank: s.rank, lp: s.lp })) };
    }
    renderChart();
  }

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
  load();
})();
