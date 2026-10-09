/* Duos : toutes les stats par duo + face-à-face des deux joueurs. SSE (debounce 1 s) + fallback 60 s. */
(function () {
  'use strict';
  const App = window.App;
  const { $, $$, api, toast, escapeHtml: esc, avatar } = App;

  const els = {
    name: $('#duos-name'),
    status: $('#duos-status'),
    updated: $('#duos-updated'),
    count: $('#duos-count'),
    actions: $('#duos-actions'),
    list: $('#duos-list'),
    unassignedSection: $('#unassigned-section'),
    unassignedHint: $('#unassigned-hint'),
    unassignedList: $('#unassigned-list'),
    cmpSection: $('#comparatif'),
    cmpTable: $('#cmp-table'),
  };

  let data = null;          // réponse de /api/duos (normalisée)
  let lastUpdated = null;
  let gamesPerDay = App.gamesPerDay;
  let loading = false;
  let targetDone = false;   // ancre #duo-<id> mise en avant une seule fois

  const num = (v) => (v === null || v === undefined || v === '' || isNaN(v) ? null : Number(v));
  const fmtDec = (v, d) => (num(v) === null ? '—' : Number(v).toFixed(d === undefined ? 2 : d));
  const mean = (values) => {
    const xs = values.map(num).filter((v) => v !== null);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  };
  const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

  /* ------------------------------------------------------------------ */
  /* Chargement                                                           */
  /* ------------------------------------------------------------------ */
  async function fetchDuos() {
    try {
      return await api('/api/duos');
    } catch (e) {
      if (e.status !== 404) throw e;
      // Repli si le serveur n'expose pas encore /api/duos : classement + état.
      const [lb, st] = await Promise.all([api('/api/leaderboard'), api('/api/state')]);
      const teams = (lb && lb.teams) || [];
      const assigned = new Set();
      teams.forEach((t) => (t.players || []).forEach((p) => assigned.add(p.player_id)));
      const unassigned = ((st && st.players) || []).filter((p) => p.active !== false && !p.team_id && !assigned.has(p.id));
      return {
        challenge: (lb && lb.challenge) || (st && st.challenge) || {},
        teams,
        unassigned_players: unassigned,
        games_per_day: (st && st.games_per_day) || null,
        generated_at: lb && lb.generated_at,
      };
    }
  }

  /* MVP du duo : LP nets, puis winrate, puis KDA (repli si le serveur ne l'a pas calculé). */
  function pickMvp(players) {
    if (players.length < 2) return players.length ? players[0].player_id : null;
    const key = (p) => [num(p.lp_net) || 0, num(p.winrate) === null ? -1 : num(p.winrate), num(p.avg_kda) === null ? -1 : num(p.avg_kda)];
    const sorted = players.slice().sort((a, b) => {
      const ka = key(a); const kb = key(b);
      return (kb[0] - ka[0]) || (kb[1] - ka[1]) || (kb[2] - ka[2]);
    });
    const ka = key(sorted[0]); const kb = key(sorted[1]);
    if (ka[0] === kb[0] && ka[1] === kb[1] && ka[2] === kb[2]) return null; // égalité parfaite : pas de MVP
    return sorted[0].player_id;
  }

  function normalizeTeam(t) {
    const players = t.players || [];
    if (t.mvp_player_id === undefined) t.mvp_player_id = pickMvp(players);
    ['avg_kda', 'avg_cs_per_min', 'avg_vision', 'avg_damage'].forEach((k) => { if (t[k] === undefined) t[k] = mean(players.map((p) => p[k])); });
    if (t.together_games === undefined) { t.together_games = 0; t.together_wins = 0; t.together_losses = 0; t.together_winrate = null; }
    if (t.mvp_top_champion_splash_url === undefined) {
      const mvp = players.find((p) => p.player_id === t.mvp_player_id);
      t.mvp_top_champion_splash_url = (mvp && mvp.top_champion_splash_url) || null;
    }
    return t;
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const d = await fetchDuos();
      d.teams = (d.teams || []).map(normalizeTeam).sort((a, b) => ((a.position || 99) - (b.position || 99)) || ((a.slot || 0) - (b.slot || 0)));
      data = d;
      gamesPerDay = d.games_per_day || (d.challenge && d.challenge.games_per_day) || App.gamesPerDay;
      lastUpdated = Date.now();
      els.updated.classList.remove('is-offline');
      render();
      tickUpdated();
      App.refreshLiveCount();
    } catch (e) {
      els.updated.classList.add('is-offline');
      els.updated.textContent = 'Hors ligne';
      if (!data) {
        els.list.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-icon">⚠️</div><div class="empty-title">Impossible de charger les duos</div>${esc(e.message)}<br><button type="button" class="btn mt-sm" onclick="location.reload()">Réessayer</button></div>`;
      }
      toast(e.message || 'Erreur de chargement', { type: 'error' });
    } finally {
      loading = false;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Rendu                                                                */
  /* ------------------------------------------------------------------ */
  function render() {
    renderHeader();
    renderTeams();
    renderComparison();
    renderUnassigned();
    highlightTarget();
  }

  function renderHeader() {
    const c = data.challenge || {};
    els.name.textContent = c.name || 'Pékin Express LoL';
    els.status.innerHTML = App.statusChip(c.status, c.start_at);
    const teams = data.teams || [];
    const nPlayers = teams.reduce((n, t) => n + (t.players || []).length, 0);
    els.count.textContent = teams.length
      ? `${plural(teams.length, 'duo', 'duos')} · ${plural(nPlayers, 'joueur', 'joueurs')} · objectif ${gamesPerDay} games par jour`
      : `Objectif ${gamesPerDay} games par jour et par joueur`;
    els.actions.innerHTML = `${teams.length > 1 && data.comparison ? '<a class="btn" href="#comparatif">📊 Comparatif</a>' : ''}` + '<a class="btn" href="/dashboard">🏆 Voir le classement</a><a class="btn btn-ghost" href="/admin#duos" title="Réservé à l’organisateur">✏️ Modifier (Admin)</a>';
  }

  function tickUpdated() {
    if (!lastUpdated) return;
    const s = Math.round((Date.now() - lastUpdated) / 1000);
    els.updated.textContent = s < 5 ? "Mis à jour à l'instant" : `Mis à jour il y a ${s < 60 ? s + ' s' : App.timeAgoSeconds(s).replace('il y a ', '')}`;
  }

  function liveBadgeHtml(liveInfo) {
    if (!liveInfo) return '';
    const start = liveInfo.game_start ? Date.parse(liveInfo.game_start) : NaN;
    const elapsed = liveInfo.elapsed_s !== undefined && liveInfo.elapsed_s !== null ? liveInfo.elapsed_s : 0;
    const startMs = !isNaN(start) && start > 0 ? start : Date.now() - elapsed * 1000;
    const icon = liveInfo.champion_icon_url ? App.champIcon({ name: liveInfo.champion_name, src: liveInfo.champion_icon_url, size: 'xs', title: liveInfo.champion_name }) : '';
    return `<span class="badge-live">${icon}<span class="dot"></span>En game${liveInfo.champion_name ? ` <span class="detail detail-champ">· ${esc(liveInfo.champion_name)}</span>` : ''} <span class="detail tnum" data-elapsed-start="${startMs}">${App.formatDuration(elapsed)}</span></span>`;
  }

  function tile(label, value, sub) {
    return `<div class="duo-tile"><div class="t-label">${label}</div><div class="t-value">${value}</div>${sub ? `<div class="t-sub">${sub}</div>` : ''}</div>`;
  }

  function renderTeams() {
    const teams = data.teams || [];
    const c = data.challenge || {};
    if (!teams.length) {
      els.list.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-icon">🤝</div><div class="empty-title">Aucun duo pour l'instant</div>L'organisateur compose les duos dans <a href="/admin#duos">Admin → Duos</a>.<br><a class="btn btn-primary" href="/admin#duos">Ouvrir l'Admin</a></div>`;
      return;
    }
    const started = c.status === 'running' || c.status === 'finished';
    els.list.innerHTML = teams.map((t) => teamHtml(t, started)).join('');
    tickElapsed();
  }

  function teamHtml(t, started) {
    const players = (t.players || []).slice(0, 2);
    const a = players[0] || null;
    const b = players[1] || null;
    const pos = t.position || 0;
    const medal = started && pos === 1;
    const wr = App.formatPct(t.winrate);
    const tg = t.together_games || 0;
    const todayParts = players.map((p) => `${p.games_today || 0}/${p.games_limit_today || p.games_limit || gamesPerDay}`);
    const windowTxt = (t.window_start || t.window_end)
      ? `Fenêtre${t.window_start ? ` du ${esc(App.formatDateTime(t.window_start))}` : ''}${t.window_end ? ` au ${esc(App.formatDateTime(t.window_end))}` : ''}`
      : '';
    const splash = t.mvp_top_champion_splash_url || null;
    return `<article class="card duo-big pos-${pos} ${splash ? 'splash-bg' : ''}" id="duo-${t.team_id}" style="--team-color:${esc(t.color || '#e5b64d')}">
      ${splash ? App.splashImg(splash, pos === 1) : ''}
      <div class="duo-band"></div>
      <header class="duo-head">
        <span class="pos-badge pos-${pos} ${medal ? 'is-medal' : ''}" title="${pos ? `${pos}${pos === 1 ? 'er' : 'e'} duo` : 'Non classé'}">${medal ? '🥇' : (pos || '–')}</span>
        <div class="duo-title">
          <h2><span class="swatch"></span><span class="name">${esc(t.name)}</span></h2>
          <div class="sub"><span>${players.length ? players.map((p) => esc(p.display_name)).join(' & ') : 'Aucun joueur pour l’instant'}</span>${t.live_count ? `<span class="badge-live" title="${t.live_count} en partie"><span class="dot"></span>${t.live_count} en game</span>` : ''}${windowTxt ? `<span>· ${windowTxt}</span>` : ''}</div>
        </div>
        <div class="duo-lp-big">
          <div class="val ${App.lpClass(t.lp_net)}">${esc(App.formatLp(t.lp_net).replace(' LP', ''))}<small>LP</small></div>
          <div class="lbl">LP nets</div>
        </div>
      </header>
      ${avgRankHtml(t, players)}
      <div class="duo-tiles">
        ${tile('Victoires / Défaites', `<span class="v-win">${t.wins || 0} V</span><span class="v-sep">–</span><span class="v-loss">${t.losses || 0} D</span>`)}
        ${tile('Winrate', wr, t.games ? '' : 'Pas encore de partie')}
        ${tile('Parties', `${t.games || 0}`, players.length ? `aujourd'hui ${todayParts.join(' · ')}` : '')}
        ${tile('KDA moyen du duo', fmtDec(t.avg_kda, 2))}
        ${tile('Parties ensemble', tg ? `${tg}` : '—', tg ? `${t.together_wins || 0} V – ${t.together_losses || 0} D · ${App.formatPct(t.together_winrate)}` : '')}
      </div>
      ${jokerHtml(t)}
      <div class="duo-face">
        <div class="face-label">Face-à-face</div>
        <div class="face-head">${facePlayer(a, t)}<div class="face-vs">VS</div>${facePlayer(b, t)}</div>
        <div class="face-rows">${faceRows(a, b)}</div>
      </div>
    </article>`;
  }

  /* « Rang moyen » du duo : emblème du tier moyen + meilleur joueur. */
  function avgRankHtml(t, players) {
    const rk = App.rk;
    if (!rk) return '';
    const avg = num(t.avg_absolute_lp);
    const tier = rk.tierFromAbsolute(avg);
    const color = t.rank_color || App.rankColor(tier);
    const top = players.find((p) => p.player_id === t.top_player_id);
    const label = avg === null ? 'Non classé' : (t.rank_label || App.rankFromAbsolute(avg));
    return `<div class="duo-avg-rank">${rk.emblem({ tier, size: 'sm', color })}<span class="lbl">Rang moyen</span><span class="rank" style="--rank-color:${esc(color)}">${esc(label)}</span>${top && t.top_player_rank_label ? `<span class="best">· meilleur <strong>${esc(top.display_name)}</strong> (${esc(t.top_player_rank_label)})</span>` : ''}</div>`;
  }

  /* ------------------------------------------------------------------ */
  /* Comparatif des duos : lignes = statistiques, colonnes = duos          */
  /* ------------------------------------------------------------------ */
  const CMP_GROUPS = [
    { label: 'Performance', keys: ['lp_net', 'lp_per_game', 'winrate', 'games', 'together_games', 'together_winrate', 'best_win_streak', 'avg_game_duration', 'surrenders'] },
    { label: 'Combat', keys: ['avg_kda', 'avg_kill_participation', 'avg_damage', 'avg_damage_share', 'multikills', 'penta_kills', 'first_bloods'] },
    { label: 'Farm & économie', keys: ['avg_cs_per_min', 'avg_gold_per_min'] },
    { label: 'Vision & objectifs', keys: ['avg_vision', 'avg_wards_placed', 'dragon_kills', 'baron_kills', 'turret_kills', 'objectives_stolen'] },
    { label: 'Rangs', keys: ['avg_absolute_lp', 'season_winrate'] },
  ];

  function renderComparison() {
    const metrics = (data.comparison && Array.isArray(data.comparison.metrics)) ? data.comparison.metrics : [];
    const teams = data.teams || [];
    els.cmpSection.hidden = !metrics.length || teams.length < 2;
    if (els.cmpSection.hidden) { els.cmpTable.innerHTML = ''; return; }
    const head = `<thead><tr><th scope="col">Statistique</th>${teams.map((t) => {
      const tier = App.rk ? App.rk.tierFromAbsolute(t.avg_absolute_lp) : null;
      const names = (t.players || []).map((p) => p.display_name).filter(Boolean).join(' & ');
      return `<th scope="col"><div class="cmp-duo" style="--team-color:${esc(t.color || '#e5b64d')}"><a href="#duo-${t.team_id}"><span class="swatch"></span>${esc(t.name || 'Duo')}</a><span class="cmp-duo-sub">${esc(names || '—')}</span>${App.rk && num(t.avg_absolute_lp) !== null ? `<span class="cmp-duo-rank rank" style="--rank-color:${esc(t.rank_color || App.rankColor(tier))}">${App.rk.emblem({ tier, size: 'xs' })}${esc(App.rk.TIER_FR[tier] || '')}</span>` : ''}</div></th>`;
    }).join('')}</tr></thead>`;
    const byKey = new Map(metrics.map((m) => [m.key, m]));
    const groups = CMP_GROUPS.map((g) => ({ label: g.label, metrics: g.keys.map((k) => byKey.get(k)).filter(Boolean) }));
    const known = new Set(CMP_GROUPS.flatMap((g) => g.keys));
    const others = metrics.filter((m) => !known.has(m.key));
    if (others.length) groups.push({ label: 'Autres', metrics: others });
    const cols = teams.length;
    const rows = groups.filter((g) => g.metrics.length).map((g) => `<tr class="cmp-group"><td>${esc(g.label)}</td><td colspan="${cols}"></td></tr>${g.metrics.map((m) => metricRow(m, teams)).join('')}`).join('');
    els.cmpTable.innerHTML = `${head}<tbody>${rows}</tbody>`;
  }

  function metricRow(m, teams) {
    const values = new Map((m.values || []).map((v) => [v.team_id, v]));
    const nums = (m.values || []).map((v) => num(v.value)).filter((v) => v !== null);
    const maxAbs = nums.length ? Math.max(...nums.map(Math.abs)) : 0;
    const hint = m.higher_is_better === false ? '<span class="cmp-hint">le plus bas gagne</span>' : '';
    const cells = teams.map((t) => {
      const v = values.get(t.team_id) || {};
      const value = num(v.value);
      const display = value === null ? '—' : (v.display !== undefined && v.display !== null && v.display !== '' ? String(v.display) : String(value));
      const best = value !== null && m.best_team_id !== null && m.best_team_id !== undefined && m.best_team_id === t.team_id;
      const worst = value !== null && !best && m.worst_team_id !== null && m.worst_team_id !== undefined && m.worst_team_id === t.team_id;
      const pct = value === null || !maxAbs ? 0 : Math.max(value === 0 ? 0 : 4, Math.round((Math.abs(value) / maxAbs) * 100));
      return `<td class="cmp-cell${best ? ' is-best' : ''}${worst ? ' is-worst' : ''}"${best ? ' title="Meilleur duo"' : worst ? ' title="Plus faible"' : ''}><div class="cmp-val">${best ? '<span class="cmp-crown" aria-label="Meilleur">👑</span>' : ''}<span class="${value === null ? 'muted' : ''}">${esc(display)}</span></div>${value === null ? '' : `<div class="cmp-bar${value < 0 ? ' is-neg' : ''}"><span style="width:${pct}%"></span></div>`}</td>`;
    }).join('');
    return `<tr><td class="cmp-label">${esc(m.label || m.key || '—')}${hint}</td>${cells}</tr>`;
  }

  /* Joker : +N parties comptées aujourd'hui pour le duo (une fois par challenge). */
  function jokerHtml(t) {
    const total = t.jokers_total || 0;
    if (!total) return '';
    const extra = t.joker_extra_games || 0;
    const base = gamesPerDay;
    const used = t.jokers || [];
    const today = used.find((j) => j.today);
    const past = used.filter((j) => !j.today);
    const who = (j) => (j.player_name ? ` par ${esc(j.player_name)}` : '');
    const at = (j) => (j.activated_at ? ` à ${esc(App.formatTime(j.activated_at))}` : '');
    const parts = [];
    if (today) {
      parts.push(`<span class="chip chip-gold">🃏 Joker actif aujourd'hui</span><span class="joker-text"><strong>${base + (today.extra_games || extra)} parties</strong> comptées aujourd'hui pour le duo (activé${who(today)}${at(today)}). Seules les parties terminées après l'activation profitent des ${today.extra_games || extra} parties en plus.</span>`);
    }
    past.forEach((j) => parts.push(`<span class="chip">🃏 Joker utilisé</span><span class="joker-text muted">le ${esc(j.day_label || j.day)}${who(j)}.</span>`));
    if (t.can_use_joker) {
      parts.push(`<button type="button" class="btn btn-sm btn-primary" data-joker="${t.team_id}">🃏 Activer le joker : ${base + extra} parties aujourd'hui</button><span class="joker-text muted">${t.jokers_left > 1 ? `${t.jokers_left} jokers restants` : '1 seul joker pour tout le challenge'} · +${extra} parties pour vous deux, aujourd'hui seulement.</span>`);
    } else if (!today && (t.jokers_left || 0) > 0) {
      parts.push(`<span class="chip">🃏 Joker disponible</span><span class="joker-text muted">+${extra} parties dans la journée, à activer pendant le challenge.</span>`);
    }
    return `<div class="duo-joker">${parts.map((x) => `<div class="joker-row">${x}</div>`).join('')}</div>`;
  }

  async function useJoker(teamId, btn) {
    const t = ((data && data.teams) || []).find((x) => x.team_id === teamId);
    if (!t) return;
    const players = (t.players || []).filter(Boolean);
    const extra = t.joker_extra_games || 0;
    const options = players.map((p) => `<option value="${p.player_id}">${esc(p.display_name)}</option>`).join('');
    const res = await App.confirm({
      title: `Activer le joker de ${t.name} ?`,
      message: `Vous aurez droit à ${gamesPerDay + extra} parties comptées aujourd'hui au lieu de ${gamesPerDay}, tous les deux. Seules les parties terminées à partir de maintenant profitent des ${extra} parties en plus. Il n'y a qu'un joker pour tout le challenge : il sera annoncé à tout le monde.`,
      extraHtml: `<div class="field mt-sm"><label for="joker-who">Qui active le joker ?</label><select id="joker-who" name="player_id" class="input">${options}</select></div>`,
      confirmText: '🃏 Activer le joker',
    });
    if (!res.ok) return;
    App.setLoading(btn, true);
    try {
      const r = await api(`/api/teams/${teamId}/joker`, { method: 'POST', body: { player_id: parseInt(res.form.player_id, 10) } });
      toast(`🃏 Joker activé : ${r.joker.limit} parties comptées aujourd'hui pour ${t.name}.`, { type: 'success', timeout: 8000 });
      await load();
    } catch (e) {
      toast(e.message || 'Joker impossible à activer.', { type: 'error' });
    } finally {
      App.setLoading(btn, false);
    }
  }

  function facePlayer(p, t) {
    if (!p) return '<div class="face-player is-empty">Place libre</div>';
    // Couronne seulement si le MVP a quelque chose à montrer (pas de MVP à 0 partie et 0 LP net)
    const mvp = t.mvp_player_id !== null && t.mvp_player_id !== undefined && t.mvp_player_id === p.player_id && !((p.games || 0) === 0 && (p.lp_net || 0) === 0);
    const rankColor = p.rank_color || App.rankColor(p.tier);
    return `<div class="face-player ${mvp ? 'is-mvp' : ''}">
      <div class="fp-avatar">${mvp ? '<span class="fp-crown" aria-hidden="true">👑</span>' : ''}${avatar({ name: p.display_name, src: p.icon_url, color: t.color, size: 'lg' })}</div>
      <a class="fp-name" href="/player/${p.player_id}">${esc(p.display_name)}</a>
      <div class="fp-rank rank" style="--rank-color:${esc(rankColor)}">${App.rankEmblem(p.rank_emblem_url, 'sm', App.tierName(p.tier))}${esc(p.rank_label || App.formatRank(p.tier, p.rank, p.lp))}</div>
      ${p.live ? liveBadgeHtml(p.live) : ''}
      ${mvp ? '<div class="fp-mvp"><span class="chip chip-gold">👑 MVP du duo</span><span class="fp-mvp-sub">le plus de LP nets</span></div>' : ''}
    </div>`;
  }

  /* Lignes comparées : `val` → HTML, `score` → nombre (null = non comparable). La plus haute gagne. */
  function streakScore(streak) {
    const m = /^([WL])(\d+)$/.exec(streak || '');
    if (!m) return 0;
    return (m[1] === 'W' ? 1 : -1) * parseInt(m[2], 10);
  }
  function streakChip(p) {
    if (!p.streak || p.streak === '—') return '<span class="muted">—</span>';
    return `<span class="chip ${p.streak.startsWith('W') ? 'chip-green' : 'chip-red'}">${esc(p.streak)}${p.hot_streak ? ' 🔥' : ''}</span>`;
  }
  function champHtml(p) {
    if (!p.top_champion) return '<span class="muted">—</span>';
    const sub = `${plural(p.top_champion_games || 0, 'partie', 'parties')}${num(p.top_champion_winrate) !== null ? ` · ${App.formatPct(p.top_champion_winrate)}` : ''}`;
    return `<span class="f-champ">${App.champIcon({ name: p.top_champion, src: p.top_champion_icon_url, size: 'sm', title: p.top_champion })}<span class="f-champ-name">${esc(p.top_champion)}</span></span><span class="f-sub">${sub}</span>`;
  }
  /* Les champions les plus joués (icônes, tooltip nom · parties · winrate), le favori mis en avant. */
  function champStripHtml(p) {
    const list = (p.champions || []).slice(0, 5);
    if (!list.length) return '<span class="muted">—</span>';
    return `<span class="champ-strip">${list.map((c, i) => App.champIcon({
      name: c.champion_name, src: c.icon_url, size: 'sm', fav: i === 0 && (c.champion_name === p.top_champion || !p.top_champion),
      title: `${c.champion_name} · ${plural(c.games || 0, 'partie', 'parties')}${num(c.winrate) !== null ? ` · ${App.formatPct(c.winrate)}` : ''}`,
    })).join('')}</span>`;
  }
  function gamesHtml(p) {
    const today = p.games_today || 0;
    const lim = p.games_limit_today || p.games_limit || gamesPerDay;
    const pct = Math.min(100, Math.round((today / Math.max(1, lim)) * 100));
    return `${p.games || 0}<span class="f-sub">aujourd'hui ${today}/${lim}${p.games_today_over_quota ? ` · <span class="lp-neg" title="Parties au-delà du quota : elles ne comptent pas">+${p.games_today_over_quota} hors quota</span>` : ''}</span><div class="progress ${today >= lim ? 'done' : ''}"><span style="width:${pct}%"></span></div>`;
  }

  const ROWS = [
    { label: 'LP nets', val: (p) => App.lpHtml(p.lp_net), score: (p) => num(p.lp_net) || 0 },
    { label: 'V – D', val: (p) => `<span class="lp-pos">${p.wins || 0}</span> – <span class="lp-neg">${p.losses || 0}</span>`, score: (p) => (p.wins || 0) - (p.losses || 0) },
    { label: 'Winrate', val: (p) => App.formatPct(p.winrate), score: (p) => num(p.winrate) },
    { label: 'Parties', val: gamesHtml, score: (p) => p.games || 0 },
    { label: 'KDA', val: (p) => fmtDec(p.avg_kda, 2), score: (p) => num(p.avg_kda) },
    { label: 'CS / min', val: (p) => fmtDec(p.avg_cs_per_min, 1), score: (p) => num(p.avg_cs_per_min) },
    { label: 'Vision', val: (p) => fmtDec(p.avg_vision, 1), score: (p) => num(p.avg_vision) },
    { label: 'Dégâts moyens', val: (p) => (num(p.avg_damage) === null ? '—' : App.formatNumber(p.avg_damage)), score: (p) => num(p.avg_damage) },
    { label: 'Série en cours', val: streakChip, score: (p) => streakScore(p.streak) },
    { label: 'Meilleure série', val: (p) => (p.best_win_streak ? `<span class="chip chip-green">W${p.best_win_streak}</span>` : '<span class="muted">—</span>'), score: (p) => p.best_win_streak || 0 },
    { label: 'Champion favori', val: champHtml, score: (p) => num(p.top_champion_winrate) },
    { label: 'Champions', val: champStripHtml, score: () => null },
  ];

  function faceRows(a, b) {
    return ROWS.map((row) => {
      let bestA = false;
      let bestB = false;
      if (a && b) {
        const sa = row.score(a);
        const sb = row.score(b);
        if (sa !== null && sb !== null && sa !== sb) { bestA = sa > sb; bestB = sb > sa; }
      }
      const cell = (p, best) => `<div class="f-val ${best ? 'is-best' : ''}">${p ? row.val(p) : '<span class="muted">—</span>'}</div>`;
      return `<div class="face-row">${cell(a, bestA)}<div class="f-label">${esc(row.label)}</div>${cell(b, bestB)}</div>`;
    }).join('');
  }

  function renderUnassigned() {
    const list = data.unassigned_players || [];
    els.unassignedSection.hidden = !list.length;
    if (!list.length) return;
    els.unassignedHint.textContent = `${plural(list.length, 'joueur', 'joueurs')} en attente d'un duo`;
    els.unassignedList.innerHTML = list.map((p) => {
      const id = p.id !== undefined ? p.id : p.player_id;
      const rankColor = p.rank_color || App.rankColor(p.tier);
      return `<a class="unassigned-item" href="/player/${id}">${avatar({ name: p.display_name, src: p.icon_url, color: rankColor, size: 'sm' })}<span>${esc(p.display_name)}</span><span class="rank" style="--rank-color:${esc(rankColor)}">${esc(p.rank_label || App.formatRank(p.tier, p.rank, p.lp))}</span>${p.is_linked === false ? '<span class="chip chip-gold">À lier</span>' : ''}</a>`;
    }).join('');
  }

  /* #duo-<id> dans l'URL : on fait défiler jusqu'à la carte et on la met en avant quelques secondes. */
  function highlightTarget() {
    if (targetDone) return;
    if (location.hash === '#comparatif') { // section masquée au chargement : on défile une fois rendue
      if (!els.cmpSection.hidden) { targetDone = true; els.cmpSection.scrollIntoView({ behavior: 'auto', block: 'start' }); }
      return;
    }
    const m = /^#duo-(\d+)$/.exec(location.hash || '');
    if (!m) { targetDone = true; return; }
    const el = document.getElementById(`duo-${m[1]}`);
    if (!el) return; // peut-être pas encore chargé
    targetDone = true;
    el.classList.add('is-target');
    el.scrollIntoView({ behavior: App.reducedMotion ? 'auto' : 'smooth', block: 'start' });
    setTimeout(() => el.classList.remove('is-target'), 4000);
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
  const refresh = App.debounce(load, 1000);
  App.connectEvents({
    match_recorded: refresh,
    rank_changed: refresh,
    poll_done: refresh,
    live_start: refresh,
    live_end: refresh,
    draw_done: refresh,
    teams_changed: refresh,
    team_updated: refresh,
    challenge_started: refresh,
    challenge_finished: refresh,
    challenge_reset: refresh,
    player_registered: refresh,
    player_linked: refresh,
    joker_used: refresh,
    joker_cancelled: refresh,
  });
  document.addEventListener('pekin:connected', () => { if (data) refresh(); });
  els.list.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-joker]');
    if (btn) useJoker(parseInt(btn.dataset.joker, 10), btn);
  });
  window.addEventListener('hashchange', () => { targetDone = false; highlightTarget(); });

  setInterval(() => { if (!document.hidden) load(); }, 60000);
  setInterval(tickElapsed, 1000);
  setInterval(tickUpdated, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });

  load();
})();
