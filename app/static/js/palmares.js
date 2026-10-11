/* Palmarès (/palmares) : podium des duos, trophées de duo, trophées individuels (gloire et honte),
   puis les victoires et exploits de chaque joueur. Une édition terminée et archivée reste
   consultable (onglets). Rafraîchissement : événements (debounce 1 s) + repli toutes les 60 s. */
(function () {
  'use strict';
  const App = window.App;
  const esc = App.escapeHtml;
  const els = {
    title: document.getElementById('pm-title'),
    sub: document.getElementById('pm-sub'),
    editions: document.getElementById('pm-editions'),
    root: document.getElementById('pm-root'),
  };
  const MEDALS = ['🥇', '🥈', '🥉'];
  let data = null;
  let selected = null; // numéro d'édition affiché (null = la plus récente)

  const num = (v) => (v === null || v === undefined || v === '' || isNaN(v) ? null : Number(v));
  const plural = (n, one, many) => `${n} ${Math.abs(n) >= 2 ? (many || `${one}s`) : one}`;

  function dateFr(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Europe/Paris' });
  }

  function editionsOf(d) {
    const list = [];
    if (d && d.current) list.push(d.current);
    ((d && d.editions) || []).forEach((e) => { if (!list.some((x) => x.edition === e.edition)) list.push(e); });
    return list;
  }

  function avatar(p, size) {
    return App.avatar({ name: p.display_name, src: p.icon_url, color: p.team_color || undefined, size: size || 'sm' });
  }

  function playerLink(p) {
    return p.archived ? esc(p.display_name) : `<a href="/player/${esc(String(p.player_id))}">${esc(p.display_name)}</a>`;
  }

  function teamChip(name, color) {
    if (!name) return '';
    return `<span class="chip chip-team pm-team-chip" style="--team-color:${esc(color || '#e5b64d')}"><span class="swatch"></span><span class="truncate">${esc(name)}</span></span>`;
  }

  /* ------------------------------------------------------------------ */
  /* Podium des duos                                                      */
  /* ------------------------------------------------------------------ */
  function podiumHtml(ed) {
    const podium = ed.podium || [];
    if (!podium.length) {
      return '<section class="card pm-hero"><div class="empty" style="border:0"><div class="empty-icon">🏆</div><div class="empty-title">Pas encore de duo classé</div>Le palmarès se remplira avec les premières parties.</div></section>';
    }
    // Ordre visuel : 2e, 1er, 3e
    const order = [podium[1], podium[0], podium[2]].filter(Boolean);
    const cards = order.map((t) => {
      const place = podium.indexOf(t);
      const players = (t.players || []).map((p) => `<div class="pm-podium-player">${avatar({ ...p, team_color: t.color }, 'md')}<div><div class="pm-name">${playerLink({ ...p, archived: ed.archived })}</div><div class="muted tnum">${esc(App.formatLp(p.lp_net))} · ${esc(p.rank_label || '')}</div></div></div>`).join('');
      return `<article class="pm-podium-card place-${place + 1}" style="--team-color:${esc(t.color || '#e5b64d')}">
        <div class="pm-medal" aria-hidden="true">${MEDALS[place]}</div>
        <div class="pm-place">${place === 0 ? 'Vainqueurs' : `${place + 1}e place`}</div>
        <h2 class="pm-team-name">${esc(t.name)}</h2>
        <div class="pm-team-lp tnum">${App.lpHtml(t.lp_net)}<span class="muted"> nets</span></div>
        <div class="pm-team-record muted tnum">${t.wins || 0} V · ${t.losses || 0} D${num(t.winrate) !== null ? ` · ${Math.round(t.winrate)} %` : ''}</div>
        <div class="pm-podium-players">${players}</div>
        <div class="pm-step" aria-hidden="true">${place + 1}</div>
      </article>`;
    }).join('');
    const rest = (ed.teams || []).slice(3).map((t) => `<li><span class="pm-rest-pos tnum">${t.position}</span>${teamChip(t.name, t.color)}<span class="muted truncate">${(t.players || []).map((p) => esc(p.display_name)).join(' & ')}</span><span class="pm-rest-lp tnum">${App.lpHtml(t.lp_net)}</span></li>`).join('');
    return `<section class="card pm-hero">
      <div class="pm-podium">${cards}</div>
      ${rest ? `<ol class="pm-rest">${rest}</ol>` : ''}
    </section>`;
  }

  /* ------------------------------------------------------------------ */
  /* Trophées                                                             */
  /* ------------------------------------------------------------------ */
  const WINNERS_SHOWN = 4;

  function trophyCard(t, archived) {
    const winners = t.winners || [];
    const shown = winners.slice(0, WINNERS_SHOWN);
    const others = winners.slice(WINNERS_SHOWN).map((w) => w.display_name);
    const body = winners.length
      ? shown.map((w) => `<div class="pm-winner">${avatar(w)}<div class="pm-winner-text"><div class="pm-name">${playerLink({ ...w, archived })}</div><div class="pm-value">${esc(w.label || '')}</div></div></div>`).join('')
      : '<div class="pm-none muted">Non décerné</div>';
    const tie = winners.length > 1
      ? `<span class="pm-tie"${others.length ? ` title="${esc(others.join(', '))}"` : ''}>ex aequo${others.length ? ` · +${others.length} autre${others.length > 1 ? 's' : ''}` : ''}</span>`
      : '';
    return `<article class="pm-trophy${winners.length ? '' : ' is-empty'}${t.category === 'honte' ? ' is-shame' : ''}">
      <div class="pm-trophy-head"><span class="pm-trophy-icon" aria-hidden="true">${esc(t.icon)}</span><div><h3>${esc(t.title)}</h3><p class="muted">${esc(t.description)}</p></div></div>
      <div class="pm-winners">${body}${tie}</div>
    </article>`;
  }

  function teamTrophyCard(t) {
    const winners = t.winners || [];
    const body = winners.length
      ? winners.map((w) => `<div class="pm-winner">${teamChip(w.name, w.color)}<div class="pm-winner-text"><div class="muted">${(w.players || []).map(esc).join(' & ')}</div><div class="pm-value">${esc(w.label || '')}</div></div></div>`).join('')
      : '<div class="pm-none muted">Non décerné</div>';
    return `<article class="pm-trophy${winners.length ? '' : ' is-empty'}">
      <div class="pm-trophy-head"><span class="pm-trophy-icon" aria-hidden="true">${esc(t.icon)}</span><div><h3>${esc(t.title)}</h3><p class="muted">${esc(t.description)}</p></div></div>
      <div class="pm-winners">${body}</div>
    </article>`;
  }

  function trophiesHtml(ed) {
    const glory = (ed.trophies || []).filter((t) => t.category !== 'honte');
    const shame = (ed.trophies || []).filter((t) => t.category === 'honte');
    const awarded = glory.filter((t) => (t.winners || []).length).length;
    return `
      <section class="section">
        <div class="section-head"><span class="section-label">Trophées de duo</span></div>
        <div class="pm-grid pm-grid-duo">${(ed.team_trophies || []).map(teamTrophyCard).join('')}</div>
      </section>
      <section class="section">
        <div class="section-head"><span class="section-label">Trophées individuels</span><span class="muted">${awarded} trophées décernés · ex aequo partagés</span></div>
        <div class="pm-grid">${glory.map((t) => trophyCard(t, ed.archived)).join('')}</div>
      </section>
      <section class="section">
        <div class="section-head"><span class="section-label">Trophées de la honte</span><span class="muted">pour rire 🤡</span></div>
        <div class="pm-grid">${shame.map((t) => trophyCard(t, ed.archived)).join('')}</div>
      </section>`;
  }

  /* ------------------------------------------------------------------ */
  /* Victoires et exploits de chacun                                      */
  /* ------------------------------------------------------------------ */
  function playerCard(p, archived) {
    const trophies = (p.trophies || []).map((t) => `<span class="pm-badge${t.category === 'honte' ? ' is-shame' : ''}" title="${esc(t.title)}">${esc(t.icon)} ${esc(t.title)}</span>`).join('');
    const exploits = (p.exploits || []).map((x) => `<li><span aria-hidden="true">${esc(x.icon)}</span><span>${esc(x.text)}</span></li>`).join('');
    const wins = (p.best_wins || []).map((w) => `<li>${App.champIcon({ name: w.champion_name || '?', src: w.champion_icon_url, size: 'sm', title: w.champion_name })}<span class="pm-win-score tnum"><strong>${w.kills}/${w.deaths}/${w.assists}</strong> <span class="muted">KDA ${esc(String(w.kda).replace('.', ','))}</span></span>${num(w.lp) !== null ? `<span class="tnum">${App.lpHtml(w.lp)}</span>` : ''}</li>`).join('');
    const rank = p.start_rank_label && p.start_rank_label !== p.rank_label
      ? `${esc(p.start_rank_label)} → <strong>${esc(p.rank_label || '')}</strong>`
      : `<strong>${esc(p.rank_label || '')}</strong>`;
    return `<article class="card pm-player" style="--team-color:${esc(p.team_color || '#e5b64d')}">
      <header class="pm-player-head">
        <span class="pm-player-pos tnum">${p.position}</span>
        ${avatar(p, 'md')}
        <div class="pm-player-id">
          <div class="pm-name pm-player-name">${playerLink({ ...p, archived })}</div>
          <div class="flex wrap" style="gap:6px">${teamChip(p.team_name, p.team_color)}<span class="muted pm-rank">${rank}</span></div>
        </div>
        <div class="pm-player-lp tnum">${App.lpHtml(p.lp_net)}<span class="muted">LP nets</span></div>
      </header>
      <div class="pm-stats tnum">
        <div><span class="pm-stat-value">${p.wins || 0}</span><span class="muted">${(p.wins || 0) > 1 ? 'victoires' : 'victoire'}</span></div>
        <div><span class="pm-stat-value">${p.losses || 0}</span><span class="muted">${(p.losses || 0) > 1 ? 'défaites' : 'défaite'}</span></div>
        <div><span class="pm-stat-value">${num(p.winrate) !== null ? `${Math.round(p.winrate)} %` : '—'}</span><span class="muted">winrate</span></div>
        <div><span class="pm-stat-value">${num(p.avg_kda) !== null ? String(p.avg_kda).replace('.', ',') : '—'}</span><span class="muted">KDA</span></div>
      </div>
      ${trophies ? `<div class="pm-badges">${trophies}</div>` : '<div class="pm-badges muted">Pas de trophée cette fois.</div>'}
      ${exploits ? `<div class="pm-block"><h4>Exploits</h4><ul class="pm-exploits">${exploits}</ul></div>` : ''}
      ${wins ? `<div class="pm-block"><h4>Plus belles victoires</h4><ul class="pm-wins">${wins}</ul></div>` : ''}
    </article>`;
  }

  function playersHtml(ed) {
    const players = ed.players || [];
    if (!players.length) return '';
    return `<section class="section">
      <div class="section-head"><span class="section-label">Victoires et exploits de chacun</span><span class="muted">${plural(players.length, 'joueur')} · classés par LP nets</span></div>
      <div class="pm-players">${players.map((p) => playerCard(p, ed.archived)).join('')}</div>
    </section>`;
  }

  /* ------------------------------------------------------------------ */
  /* Rendu                                                                */
  /* ------------------------------------------------------------------ */
  function renderEditions(list, ed) {
    if (list.length < 2) { els.editions.hidden = true; els.editions.innerHTML = ''; return; }
    els.editions.hidden = false;
    els.editions.innerHTML = list.map((e) => `<button type="button" class="btn btn-sm ${e.edition === ed.edition ? 'btn-primary' : 'btn-ghost'}" role="tab" aria-selected="${e.edition === ed.edition}" data-edition="${e.edition}">${esc(e.edition_label)}</button>`).join('');
  }

  function render() {
    const list = editionsOf(data);
    if (!list.length) {
      els.title.textContent = 'Palmarès';
      els.sub.textContent = 'Le palmarès apparaîtra quand le challenge aura commencé.';
      els.editions.hidden = true;
      els.root.innerHTML = '<section class="card"><div class="empty" style="border:0"><div class="empty-icon">🏆</div><div class="empty-title">Pas encore de palmarès</div>Revenez une fois le challenge lancé : vainqueurs, trophées et exploits de chacun s’afficheront ici.</div></section>';
      return;
    }
    const ed = list.find((e) => e.edition === selected) || list[0];
    renderEditions(list, ed);
    els.title.innerHTML = `🏆 Palmarès · ${esc(ed.edition_label)}`;
    const dates = [dateFr(ed.start_at), dateFr(ed.end_at)].filter(Boolean);
    const when = dates.length === 2 && dates[0] !== dates[1] ? `du ${dates[0]} au ${dates[1]}` : (dates[0] ? `le ${dates[0]}` : '');
    els.sub.innerHTML = `${esc(ed.name || '')}${when ? ` · ${esc(when)}` : ''} ${ed.final ? '<span class="chip chip-green">Résultats définitifs</span>' : '<span class="chip chip-gold">Provisoire : le challenge est en cours</span>'}`;
    els.root.innerHTML = podiumHtml(ed) + trophiesHtml(ed) + playersHtml(ed);
  }

  async function load() {
    try {
      data = await App.api('/api/palmares');
      render();
    } catch (e) {
      if (!data) els.root.innerHTML = `<section class="card"><div class="empty" style="border:0"><div class="empty-title">Palmarès indisponible</div>${esc(e.message)}</div></section>`;
    }
  }

  els.editions.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-edition]');
    if (!btn) return;
    selected = parseInt(btn.dataset.edition, 10);
    render();
  });

  const refresh = App.debounce(load, 1000);
  App.connectEvents({
    match_recorded: refresh,
    rank_changed: refresh,
    double_lp: refresh,
    double_lp_changed: refresh,
    teams_changed: refresh,
    challenge_started: refresh,
    challenge_finished: refresh,
    challenge_reset: refresh,
  });
  document.addEventListener('pekin:connected', () => { if (data) refresh(); });
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  load();
})();
