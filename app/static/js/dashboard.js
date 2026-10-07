/* Classement : duos, tableau joueurs, graphe LP, feed, panneau live. SSE + fallback 60 s. */
(function () {
  'use strict';
  const App = window.App;
  const { $, $$, api, toast, escapeHtml: esc, avatar } = App;

  const els = {
    name: $('#dash-name'),
    status: $('#dash-status'),
    updated: $('#dash-updated'),
    day: $('#dash-day'),
    actions: $('#dash-actions'),
    teamsGrid: $('#teams-grid'),
    playersBody: $('#players-body'),
    feed: $('#feed'),
    liveList: $('#live-list'),
    liveBadge: $('#live-badge'),
    legend: $('#lp-legend'),
    chartEmpty: $('#chart-empty'),
    chartHint: $('#chart-hint'),
    chartCanvas: $('#lp-chart'),
  };

  let leaderboard = null;
  let history = null;
  let feed = null;
  let live = null;
  let sortKey = 'lp_net';
  let lastUpdated = null;
  let chart = null;
  let gamesPerDay = App.gamesPerDay;

  /* ------------------------------------------------------------------ */
  /* Chargement                                                           */
  /* ------------------------------------------------------------------ */
  async function loadLeaderboard() {
    leaderboard = await api(`/api/leaderboard?sort=${encodeURIComponent(sortKey)}`);
    if (leaderboard && leaderboard.challenge && leaderboard.challenge.games_per_day) gamesPerDay = leaderboard.challenge.games_per_day;
    renderHeader();
    renderTeams();
    renderPlayers();
  }
  async function loadHistory() {
    history = await api('/api/lp-history');
    renderChart();
  }
  async function loadFeed() {
    feed = await api('/api/feed?limit=30');
    renderFeed();
  }
  async function loadLive() {
    live = await api('/api/live');
    renderLive();
    App.setLiveCount((live && live.live && live.live.length) || 0);
  }

  async function loadAll() {
    const results = await Promise.allSettled([loadLeaderboard(), loadHistory(), loadFeed(), loadLive()]);
    const failed = results.filter((r) => r.status === 'rejected');
    if (failed.length) {
      els.updated.classList.add('is-offline');
      els.updated.textContent = 'Hors ligne';
      toast(failed[0].reason && failed[0].reason.message ? failed[0].reason.message : 'Erreur de chargement', { type: 'error' });
      if (!leaderboard) {
        els.teamsGrid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-icon">⚠️</div><div class="empty-title">Impossible de charger le classement</div>${esc(failed[0].reason && failed[0].reason.message)}<br><button type="button" class="btn mt-sm" onclick="location.reload()">Réessayer</button></div>`;
        els.playersBody.innerHTML = '<tr><td colspan="9" class="muted center">—</td></tr>';
      }
    } else {
      lastUpdated = Date.now();
      els.updated.classList.remove('is-offline');
      tickUpdated();
    }
  }

  /* ------------------------------------------------------------------ */
  /* En-tête                                                              */
  /* ------------------------------------------------------------------ */
  function renderHeader() {
    const c = leaderboard.challenge || {};
    els.name.textContent = c.name || 'Pékin Express LoL';
    els.status.innerHTML = App.statusChip(c.status);
    if (c.status === 'running' && c.start_at) {
      const day = Math.floor((Date.now() - Date.parse(c.start_at)) / 86400000) + 1;
      els.day.textContent = day >= 1 ? `Jour ${day} · objectif ${gamesPerDay} games par joueur` : `Objectif ${gamesPerDay} games par jour`;
    } else if (c.status === 'finished') {
      els.day.textContent = c.end_at ? `Terminé ${App.formatDateTime(c.end_at)}` : 'Terminé';
    } else {
      els.day.textContent = `Objectif ${gamesPerDay} games par jour et par joueur`;
    }
    if (c.status === 'registration') {
      els.actions.innerHTML = '<a class="btn" href="/">Inscriptions</a><a class="btn btn-primary" href="/duos">🤝 Voir les duos</a>';
    } else if (c.status === 'drawn') {
      els.actions.innerHTML = '<a class="btn" href="/duos">🤝 Voir les duos</a><a class="btn btn-primary" href="/admin" title="Réservé à l’organisateur">🚀 Démarrer le challenge</a>';
    } else {
      els.actions.innerHTML = '<a class="btn" href="/duos">🤝 Voir les duos</a>';
    }
  }

  function tickUpdated() {
    if (!lastUpdated) return;
    const s = Math.round((Date.now() - lastUpdated) / 1000);
    els.updated.textContent = s < 5 ? "Mis à jour à l'instant" : `Mis à jour il y a ${s < 60 ? s + ' s' : App.timeAgoSeconds(s).replace('il y a ', '')}`;
  }

  /* ------------------------------------------------------------------ */
  /* Duos                                                                 */
  /* ------------------------------------------------------------------ */
  function liveBadgeHtml(liveInfo) {
    if (!liveInfo) return '';
    const start = liveInfo.game_start ? Date.parse(liveInfo.game_start) : NaN;
    const elapsed = liveInfo.elapsed_s !== undefined && liveInfo.elapsed_s !== null ? liveInfo.elapsed_s : 0;
    const startMs = !isNaN(start) && start > 0 ? start : Date.now() - elapsed * 1000;
    const icon = liveInfo.champion_icon_url ? App.champIcon({ name: liveInfo.champion_name, src: liveInfo.champion_icon_url, size: 'xs', title: liveInfo.champion_name }) : '';
    return `<span class="badge-live">${icon}<span class="dot"></span>En game${liveInfo.champion_name ? ` <span class="detail">· ${esc(liveInfo.champion_name)}</span>` : ''} <span class="detail tnum" data-elapsed-start="${startMs}">${App.formatDuration(elapsed)}</span></span>`;
  }

  function playerRowHtml(p, color) {
    const today = p.games_today || 0;
    const limit = p.games_limit || gamesPerDay;
    const pct = Math.min(100, Math.round((today / Math.max(1, limit)) * 100));
    const rankColor = p.rank_color || App.rankColor(p.tier);
    return `<div class="team-player">
      <div class="tp-row">
        ${avatar({ name: p.display_name, src: p.icon_url, color, size: 'sm' })}
        <div class="grow truncate"><div class="tp-line"><a class="tp-name" href="/player/${p.player_id}">${esc(p.display_name)}</a>${p.top_champion ? App.champIcon({ name: p.top_champion, src: p.top_champion_icon_url, size: 'xs', className: 'tp-champ', title: `Champion favori : ${p.top_champion} · ${p.top_champion_games || 0} ${p.top_champion_games > 1 ? 'parties' : 'partie'}` }) : ''}</div>
          <div class="tp-rank rank" style="--rank-color:${esc(rankColor)}">${App.rankEmblem(p.rank_emblem_url, 'sm', App.tierName(p.tier))}${esc(p.rank_label || App.formatRank(p.tier, p.rank, p.lp))}</div></div>
        ${App.lpHtml(p.lp_net, 'tp-lp')}
      </div>
      <div class="tp-today"><span>aujourd'hui ${today}/${limit}</span><div class="progress ${today >= limit ? 'done' : ''}"><span style="width:${pct}%"></span></div></div>
      ${p.live ? liveBadgeHtml(p.live) : ''}
    </div>`;
  }

  function renderTeams() {
    const teams = (leaderboard.teams || []).slice().sort((a, b) => (a.position || 99) - (b.position || 99));
    if (!teams.length) {
      const status = leaderboard.challenge && leaderboard.challenge.status;
      els.teamsGrid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-icon">🤝</div><div class="empty-title">Aucun duo pour l'instant</div>${status === 'running' || status === 'finished' ? 'Les duos apparaîtront ici.' : 'L\'organisateur compose les duos dans <a href="/admin#duos">Admin → Duos</a>.'}<br><a class="btn btn-primary" href="/duos">Voir les duos</a></div>`;
      return;
    }
    els.teamsGrid.innerHTML = teams.map((t) => {
      const wr = App.formatPct(t.winrate);
      return `<article class="card team-card is-link pos-${t.position}" style="--team-color:${esc(t.color)}" data-href="/duos#duo-${t.team_id}" tabindex="0" role="link" aria-label="${esc(t.name)} : voir les stats du duo">
        <div class="team-head"><span class="pos-badge pos-${t.position}">${t.position}</span><span class="team-name truncate">${esc(t.name)}</span>${t.live_count ? `<span class="badge-live" title="${t.live_count} en partie"><span class="dot"></span>${t.live_count}</span>` : ''}<span class="team-more" aria-hidden="true" title="Voir le détail du duo">→</span></div>
        <div class="team-lp ${App.lpClass(t.lp_net)}">${esc(App.formatLp(t.lp_net).replace(' LP', ''))}<small>LP</small></div>
        <div class="team-record"><span><strong>${t.wins}</strong> V – <strong>${t.losses}</strong> D</span><span>${wr === '—' ? 'Pas de partie' : `<strong>${wr}</strong> winrate`}</span><span><strong>${t.games}</strong> ${t.games > 1 ? 'parties' : 'partie'}</span></div>
        <div class="team-players">${(t.players || []).map((p) => playerRowHtml(p, t.color)).join('')}</div>
      </article>`;
    }).join('');
  }

  /* Clic (ou Entrée) sur une carte de duo → page Duos, sauf sur un lien interne (fiche joueur). */
  function cardNavigate(e) {
    const card = e.target.closest('.team-card[data-href]');
    if (!card || e.target.closest('a')) return;
    if (e.type === 'keydown' && e.key !== 'Enter') return;
    window.location.href = card.dataset.href;
  }
  els.teamsGrid.addEventListener('click', cardNavigate);
  els.teamsGrid.addEventListener('keydown', cardNavigate);

  /* ------------------------------------------------------------------ */
  /* Tableau joueurs                                                      */
  /* ------------------------------------------------------------------ */
  function sortPlayers(players) {
    const val = (p) => {
      switch (sortKey) {
        case 'winrate': return p.winrate === null || p.winrate === undefined ? -1 : p.winrate;
        case 'games': return p.games || 0;
        case 'kda': return p.avg_kda === null || p.avg_kda === undefined ? -1 : p.avg_kda;
        default: return p.lp_net || 0;
      }
    };
    return players.slice().sort((a, b) => val(b) - val(a) || (b.lp_net || 0) - (a.lp_net || 0) || (b.games || 0) - (a.games || 0));
  }

  function teamOf(teamId) {
    return (leaderboard.teams || []).find((t) => t.team_id === teamId);
  }

  function renderPlayers() {
    const players = sortPlayers(leaderboard.players || []);
    $$('#players-table th.sortable').forEach((th) => th.classList.toggle('is-active', th.dataset.sort === sortKey));
    if (!players.length) {
      els.playersBody.innerHTML = '<tr><td colspan="9"><div class="empty" style="border:0"><div class="empty-title">Aucun joueur inscrit</div><a class="btn btn-primary" href="/">Inscrire les joueurs</a></div></td></tr>';
      return;
    }
    els.playersBody.innerHTML = players.map((p, i) => {
      const team = teamOf(p.team_id);
      const color = team ? team.color : (p.rank_color || App.rankColor(p.tier));
      const streak = p.streak && p.streak !== '—' ? `<span class="chip ${p.streak.startsWith('W') ? 'chip-green' : 'chip-red'}">${esc(p.streak)}${p.hot_streak ? ' 🔥' : ''}</span>` : '<span class="muted">—</span>';
      return `<tr class="${p.active === false ? 'is-stale' : ''}">
        <td class="muted tnum">${i + 1}</td>
        <td><div class="cell-player">${avatar({ name: p.display_name, src: p.icon_url, color, size: 'sm' })}<a href="/player/${p.player_id}">${esc(p.display_name)}</a>${p.top_champion ? App.champIcon({ name: p.top_champion, src: p.top_champion_icon_url, size: 'sm', title: `Champion favori : ${p.top_champion} · ${p.top_champion_games || 0} ${p.top_champion_games > 1 ? 'parties' : 'partie'}` }) : ''}${team ? `<span class="chip chip-team" style="--team-color:${esc(team.color)}" title="${esc(team.name)}"><span class="swatch"></span>${esc(team.name.replace(/^Duo\s+/i, ''))}</span>` : ''}${p.live ? '<span class="badge-live"><span class="dot"></span>Live</span>' : ''}</div></td>
        <td><span class="cell-rank">${App.rankEmblem(p.rank_crest_url, 'sm', App.tierName(p.tier))}<span class="rank" style="--rank-color:${esc(p.rank_color || App.rankColor(p.tier))}">${esc(p.rank_label || App.formatRank(p.tier, p.rank, p.lp))}</span></span></td>
        <td class="num">${App.lpHtml(p.lp_net)}</td>
        <td class="num">${p.games || 0}<span class="muted"> · ${p.games_today || 0}/${p.games_limit || gamesPerDay} auj.</span></td>
        <td class="num"><span class="lp-pos">${p.wins || 0}</span> – <span class="lp-neg">${p.losses || 0}</span></td>
        <td class="num">${App.formatPct(p.winrate)}</td>
        <td class="num">${p.avg_kda === null || p.avg_kda === undefined ? '—' : Number(p.avg_kda).toFixed(2)}</td>
        <td class="num">${streak}</td>
      </tr>`;
    }).join('');
  }

  $$('#players-table th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      sortKey = th.dataset.sort;
      if (leaderboard) renderPlayers();
    });
  });

  /* ------------------------------------------------------------------ */
  /* Graphe LP (Chart.js, axe x linéaire sur timestamps)                  */
  /* ------------------------------------------------------------------ */
  const hidden = new Set(); // player_id masqués via la légende

  function tickTime(v, range) {
    const d = new Date(v);
    if (isNaN(d.getTime())) return '';
    if (range > 2 * 86400000) return d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  }

  function buildDatasets() {
    const series = (history && history.series) || [];
    const seenTeam = {};
    return series.map((s) => {
      const key = s.team_id === null || s.team_id === undefined ? `p${s.player_id}` : `t${s.team_id}`;
      const nth = (seenTeam[key] = (seenTeam[key] || 0) + 1);
      const color = s.color || App.rankColor(null);
      const points = (s.points || []).map((pt) => ({ x: Date.parse(pt.t), y: pt.absolute_lp, tier: pt.tier, rank: pt.rank, lp: pt.lp })).filter((pt) => !isNaN(pt.x) && pt.y !== null && pt.y !== undefined);
      return {
        label: s.display_name,
        playerId: s.player_id,
        data: points,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        borderDash: nth > 1 ? [6, 4] : [],
        pointRadius: points.length > 60 ? 0 : 2.5,
        pointHoverRadius: 5,
        pointBorderColor: '#121722',
        pointBorderWidth: 2,
        tension: 0.15,
        stepped: false,
        spanGaps: true,
        hidden: hidden.has(s.player_id),
      };
    });
  }

  function renderLegend(datasets) {
    els.legend.innerHTML = datasets.map((d) => `<button type="button" class="${d.hidden ? 'is-off' : ''}" data-player="${d.playerId}" style="--key-color:${esc(d.borderColor)}"><span class="key ${d.borderDash.length ? 'dashed' : ''}"></span>${esc(d.label)}</button>`).join('');
    $$('button', els.legend).forEach((b) => b.addEventListener('click', () => {
      const id = parseInt(b.dataset.player, 10);
      if (hidden.has(id)) hidden.delete(id); else hidden.add(id);
      b.classList.toggle('is-off', hidden.has(id));
      if (chart) {
        chart.data.datasets.forEach((d) => { d.hidden = hidden.has(d.playerId); });
        chart.update();
      }
    }));
  }

  function renderChart() {
    const datasets = buildDatasets();
    const hasData = datasets.some((d) => d.data.length > 0);
    if (!hasData || typeof window.Chart === 'undefined') {
      els.chartEmpty.hidden = false;
      els.chartEmpty.innerHTML = typeof window.Chart === 'undefined'
        ? '<div class="empty-title">Graphe indisponible</div>La bibliothèque de graphes n\'a pas pu être chargée.'
        : '<div class="empty-icon">📈</div><div class="empty-title">Pas encore de données</div>Les LP apparaîtront dès les premières parties.';
      els.chartCanvas.parentElement.hidden = true;
      els.legend.innerHTML = '';
      if (chart) { chart.destroy(); chart = null; }
      return;
    }
    els.chartEmpty.hidden = true;
    els.chartCanvas.parentElement.hidden = false;
    renderLegend(datasets);
    els.chartHint.textContent = 'LP absolus · trait pointillé = 2ᵉ joueur du duo';

    const xs = datasets.flatMap((d) => d.data.map((p) => p.x));
    const range = Math.max(...xs) - Math.min(...xs);

    if (chart) {
      chart.data.datasets = datasets;
      chart.options.scales.x.ticks.callback = (v) => tickTime(v, range);
      chart.update('none');
      return;
    }
    chart = new window.Chart(els.chartCanvas.getContext('2d'), {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: App.reducedMotion ? false : { duration: 300 },
        interaction: { mode: 'nearest', axis: 'x', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1d2434',
            titleColor: '#e6e9ef',
            bodyColor: '#e6e9ef',
            borderColor: 'rgba(255,255,255,0.12)',
            borderWidth: 1,
            padding: 10,
            displayColors: true,
            boxPadding: 4,
            callbacks: {
              title: (items) => (items.length ? App.formatDateTime(items[0].parsed.x) : ''),
              label: (item) => {
                const raw = item.raw || {};
                const rank = raw.tier ? App.formatRank(raw.tier, raw.rank, raw.lp) : App.rankFromAbsolute(item.parsed.y);
                return ` ${item.dataset.label} · ${rank}`;
              },
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            grid: { color: 'rgba(255,255,255,0.05)', drawTicks: false },
            border: { color: 'rgba(255,255,255,0.08)' },
            ticks: { color: '#8b93a7', maxTicksLimit: 8, maxRotation: 0, autoSkip: true, padding: 8, callback: (v) => tickTime(v, range), font: { size: 11 } },
          },
          y: {
            grid: { color: 'rgba(255,255,255,0.05)', drawTicks: false },
            border: { color: 'rgba(255,255,255,0.08)' },
            ticks: { color: '#8b93a7', maxTicksLimit: 7, padding: 8, callback: (v) => App.rankFromAbsolute(v), font: { size: 11 } },
          },
        },
      },
    });
  }

  /* ------------------------------------------------------------------ */
  /* Feed                                                                 */
  /* ------------------------------------------------------------------ */
  function renderFeed() {
    const items = (feed && feed.items) || [];
    if (!items.length) {
      els.feed.innerHTML = '<div class="empty" style="border:0;padding:24px 0"><div class="empty-icon">🕹️</div><div class="empty-title">Pas encore de partie enregistrée</div>Les parties classées apparaîtront ici dès qu\'elles seront terminées.</div>';
      return;
    }
    els.feed.innerHTML = items.map((m) => {
      const ago = m.ago_s !== undefined && m.ago_s !== null ? App.timeAgoSeconds(m.ago_s) : App.timeAgo(m.game_start);
      const kda = `${m.kills}/${m.deaths}/${m.assists}`;
      const hasItems = Array.isArray(m.item_urls) && m.item_urls.some(Boolean);
      return `<div class="feed-item ${m.is_remake ? 'is-remake' : ''}">
        ${App.champIcon({ name: m.champion_name || '?', src: m.champion_icon_url, size: 'md', level: m.champ_level, title: m.champion_name })}
        <div class="feed-main">
          <div class="feed-line"><a class="feed-name" href="/player/${m.player_id}">${esc(m.display_name)}</a>${m.team_color ? `<span class="swatch" style="--team-color:${esc(m.team_color)}"></span>` : ''}<span class="wl-pill ${m.is_remake ? 'remake' : m.win ? 'win' : 'loss'}">${m.is_remake ? 'R' : m.win ? 'V' : 'D'}</span></div>
          <div class="feed-sub"><span>${esc(m.champion_name || '')}</span>${m.position ? App.posIcon(m.position_icon_url, m.position) : ''}<span class="tnum">${kda}</span><span>${App.formatDuration(m.game_duration)}</span>${m.queue && m.queue !== 'SOLO' ? `<span>${esc(App.queueLabel(m.queue))}</span>` : ''}</div>
          ${hasItems ? `<div class="feed-items">${App.itemRow(m.item_urls, { size: 'sm' })}</div>` : ''}
        </div>
        <div class="feed-lp">${m.is_remake ? '<span class="chip">Remake</span>' : App.lpHtml(m.lp_change)}<span class="ago">${esc(ago)}</span></div>
      </div>`;
    }).join('');
  }

  /* ------------------------------------------------------------------ */
  /* Live                                                                 */
  /* ------------------------------------------------------------------ */
  function renderLive() {
    const items = (live && live.live) || [];
    els.liveBadge.hidden = !items.length;
    if (!items.length) {
      els.liveList.innerHTML = '<div class="empty" style="border:0;padding:20px 0"><div class="empty-icon">💤</div><div class="empty-title">Personne en game</div>Tu seras prévenu dès qu\'un joueur lance une partie.</div>';
      return;
    }
    els.liveList.innerHTML = items.map((l) => {
      const start = l.game_start ? Date.parse(l.game_start) : NaN;
      const startMs = !isNaN(start) && start > 0 ? start : Date.now() - (l.elapsed_s || 0) * 1000;
      return `<div class="live-item">
        ${App.loadingArt({ name: l.champion_name || '?', src: l.champion_loading_url, iconSrc: l.champion_icon_url, title: l.champion_name })}
        <div class="live-main truncate">
          <div class="live-name"><a href="/player/${l.player_id}" style="color:inherit">${esc(l.display_name)}</a>${l.team_name ? ` <span class="chip chip-team" style="--team-color:${esc(l.team_color || '#e5b64d')}"><span class="swatch"></span>${esc(l.team_name)}</span>` : ''}</div>
          <div class="live-sub">${esc(l.champion_name || 'Champion inconnu')} · ${esc(App.queueLabel(l.queue_id, l.game_mode))}</div>
        </div>
        <div class="live-time" data-elapsed-start="${startMs}">${App.formatDuration(l.elapsed_s || 0)}</div>
      </div>`;
    }).join('');
  }

  function tickElapsed() {
    const now = Date.now();
    $$('[data-elapsed-start]').forEach((el) => {
      const start = parseInt(el.dataset.elapsedStart, 10);
      if (!isNaN(start)) el.textContent = App.formatDuration((now - start) / 1000);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Rafraîchissements                                                    */
  /* ------------------------------------------------------------------ */
  const safe = (fn) => () => fn().then(() => { lastUpdated = Date.now(); tickUpdated(); }).catch((e) => { els.updated.classList.add('is-offline'); els.updated.textContent = 'Hors ligne'; console.warn(e); });
  const refreshScores = App.debounce(safe(() => Promise.all([loadLeaderboard(), loadHistory(), loadFeed()])), 1000);
  const refreshLive = App.debounce(safe(() => Promise.all([loadLive(), loadLeaderboard()])), 1000);
  const refreshAll = App.debounce(safe(loadAll), 1000);

  App.connectEvents({
    match_recorded: refreshScores,
    rank_changed: refreshScores,
    poll_done: refreshScores,
    live_start: refreshLive,
    live_end: refreshLive,
    draw_done: refreshAll,
    challenge_started: refreshAll,
    challenge_finished: refreshAll,
    challenge_reset: refreshAll,
    teams_changed: refreshAll,
    player_registered: refreshAll,
    player_linked: refreshAll,
  });
  document.addEventListener('pekin:connected', () => { if (leaderboard) refreshAll(); });

  setInterval(() => { if (!document.hidden) loadAll(); }, 60000);
  setInterval(() => { tickElapsed(); }, 1000);
  setInterval(tickUpdated, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loadAll(); });

  loadAll();
})();
