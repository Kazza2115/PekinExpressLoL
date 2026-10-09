/* Accueil : inscription, liaison des comptes, grille des 8 places, duos. */
(function () {
  'use strict';
  const { $, $$, api, toast, escapeHtml: esc, avatar } = window.App;
  const App = window.App;

  const MAX_PLAYERS = 8;
  let state = null;
  let loading = false;

  const els = {
    name: $('#hero-name'),
    status: $('#hero-status'),
    count: $('#hero-count'),
    cta: $('#hero-cta'),
    registerCard: $('#register-card'),
    form: $('#register-form'),
    formError: $('#register-error'),
    submit: $('#register-submit'),
    demoFill: $('#btn-demo-fill'),
    demoCard: $('#demo-card'),
    duosSection: $('#duos-section'),
    duosGrid: $('#duos-grid'),
    duosHint: $('#duos-hint'),
    slots: $('#slots'),
    playersHint: $('#players-hint'),
    side: $('#home-side'),
    root: $('#home-root'),
  };

  /* ------------------------------------------------------------------ */
  function linkedPlayers(players) {
    return players.filter((p) => p.active !== false && p.is_linked);
  }

  function playerById(id) {
    return (state.players || []).find((p) => p.id === id);
  }

  function renderHero() {
    const c = state.challenge || {};
    const players = state.players || [];
    els.name.textContent = c.name || 'Pékin Express LoL';
    els.status.innerHTML = App.statusChip(c.status, c.start_at);
    const active = players.filter((p) => p.active !== false);
    els.count.innerHTML = `<strong>${active.length}</strong> / ${Math.max(MAX_PLAYERS, active.length)} joueurs`;

    const status = c.status || 'registration';
    const linked = linkedPlayers(players);
    const teams = state.teams || [];
    let html = '';
    if (status === 'registration') {
      html += '<a class="btn btn-primary btn-lg" href="/duos" id="cta-duos">🤝 Voir les duos</a>';
      if (teams.length) {
        html += `<span class="help">${teams.length} ${teams.length > 1 ? 'duos formés' : 'duo formé'} · ${linked.length} ${linked.length > 1 ? 'joueurs liés' : 'joueur lié'}. L'organisateur compose les duos dans l'Admin.</span>`;
      } else {
        html += `<span class="help">L'organisateur compose les duos dans <a href="/admin#duos">l'Admin</a>.${linked.length < players.filter((p) => p.active !== false).length ? ' Pense à lier ton compte avant.' : ''}</span>`;
      }
    } else if (status === 'drawn') {
      html += '<div class="btn-row"><a class="btn" href="/duos">🤝 Voir les duos</a><a class="btn" href="/dashboard">Voir le classement</a><button type="button" class="btn btn-primary btn-lg" id="cta-start">🚀 Démarrer le challenge</button></div>';
      html += '<span class="help">Réservé à l\'organisateur : lance le suivi des parties.</span>';
    } else if (status === 'running') {
      html += '<div class="btn-row"><a class="btn" href="/duos">🤝 Les duos</a><a class="btn btn-primary btn-lg" href="/dashboard">Voir le classement</a></div>';
      if (c.start_at) html += `<span class="help">${Date.parse(c.start_at) > Date.now() ? 'Démarre' : 'Démarré'} ${esc(App.formatDateTime(c.start_at))}${c.end_at ? ` · fin ${esc(App.formatDateTime(c.end_at))}` : ''}</span>`;
    } else {
      html += '<div class="btn-row"><a class="btn" href="/duos">🤝 Les duos</a><a class="btn btn-primary btn-lg" href="/dashboard">Voir le classement final</a></div>';
    }
    els.cta.innerHTML = html;

    const startBtn = $('#cta-start');
    if (startBtn) startBtn.addEventListener('click', () => startChallenge(startBtn));
  }

  function renderSlots() {
    const players = (state.players || []).slice().sort((a, b) => (a.id || 0) - (b.id || 0));
    const status = (state.challenge && state.challenge.status) || 'registration';
    const total = Math.max(MAX_PLAYERS, players.length);
    const teamById = {};
    (state.teams || []).forEach((t) => { teamById[t.id] = t; });
    let html = '';
    for (let i = 0; i < total; i++) {
      const p = players[i];
      if (!p) {
        html += `<div class="card slot slot-empty"><span class="slot-num">Place ${i + 1}</span><span>Place libre</span></div>`;
        continue;
      }
      const team = p.team_id ? teamById[p.team_id] : null;
      const color = team ? team.color : (p.rank_color || App.rankColor(p.tier));
      const inactive = p.active === false;
      html += `<div class="card slot ${inactive ? 'is-stale' : ''}" data-player-id="${p.id}">
        <div class="slot-head">
          ${avatar({ name: p.display_name, src: p.icon_url, color, size: 'lg' })}
          <div class="grow">
            <div class="slot-name truncate"><a href="/player/${p.id}" style="color:inherit">${esc(p.display_name)}</a></div>
            <div class="slot-riot truncate">${p.riot_id ? esc(p.riot_id) : '<span class="muted">Compte non renseigné</span>'}</div>
            <div class="rank" style="--rank-color:${esc(p.rank_color || App.rankColor(p.tier))};font-size:13px">${App.rankEmblem(p.rank_emblem_url, 'sm', App.tierName(p.tier))}${esc(p.rank_label || App.formatRank(p.tier, p.rank, p.lp))}</div>
          </div>
        </div>
        ${p.link_error && !p.is_linked ? `<div class="link-error">⚠ ${esc(p.link_error)}</div>` : ''}
        ${!p.is_linked && (status === 'registration' || status === 'drawn') ? `
          <form class="slot-link-form" data-link-form="${p.id}">
            <input type="text" name="riot_id" placeholder="Pseudo#TAG" value="${esc(p.riot_id || '')}" aria-label="Riot ID de ${esc(p.display_name)}" autocapitalize="off" spellcheck="false">
            <button type="submit" class="btn btn-sm btn-primary">Lier mon compte</button>
          </form>` : ''}
        <div class="slot-foot">
          ${team ? `<span class="chip chip-team" style="--team-color:${esc(team.color)}"><span class="swatch"></span>${esc(team.name)}</span>` : inactive ? '<span class="chip">Inactif</span>' : '<span></span>'}
          ${p.is_linked ? '<span class="chip chip-green">✓ Compte lié</span>' : '<span class="chip chip-gold">À lier</span>'}
        </div>
      </div>`;
    }
    els.slots.innerHTML = html;
    $$('[data-link-form]', els.slots).forEach((form) => form.addEventListener('submit', onLink));
  }

  function renderDuos() {
    const teams = (state.teams || []).slice().sort((a, b) => (a.slot || 0) - (b.slot || 0));
    const status = (state.challenge && state.challenge.status) || 'registration';
    if (!teams.length) {
      els.duosSection.hidden = true;
      return;
    }
    els.duosSection.hidden = false;
    els.duosHint.innerHTML = {
      registration: 'Composés par l\'organisateur · <a href="/duos">toutes les stats →</a>',
      drawn: 'En attente du départ · <a href="/duos">toutes les stats →</a>',
      running: (state.challenge && state.challenge.start_at && Date.parse(state.challenge.start_at) > Date.now()
        ? `Départ ${esc(App.formatDateTime(state.challenge.start_at))}`
        : 'Le challenge est en cours') + ' · <a href="/duos">toutes les stats →</a>',
    }[status] || 'Classement final disponible · <a href="/duos">toutes les stats →</a>';
    els.duosGrid.innerHTML = teams.map((t) => {
      const players = (t.player_ids || []).map(playerById).filter(Boolean);
      return `<a class="card duo-card" href="/duos#duo-${t.id}" style="--team-color:${esc(t.color)}" aria-label="${esc(t.name)} : voir les stats du duo">
        <div class="duo-name"><span class="swatch"></span>${esc(t.name)}</div>
        <div class="duo-players">
          ${players.length ? players.map((p) => `<div class="duo-player">
            ${avatar({ name: p.display_name, src: p.icon_url, color: t.color })}
            <div class="grow truncate"><div class="name">${esc(p.display_name)}</div>
            <div class="sub rank" style="--rank-color:${esc(p.rank_color || App.rankColor(p.tier))}">${App.rankEmblem(p.rank_emblem_url, 'sm', App.tierName(p.tier))}${esc(p.rank_label || App.formatRank(p.tier, p.rank, p.lp))}</div></div>
          </div>`).join('') : '<div class="duo-player muted">Aucun joueur pour l\'instant</div>'}
        </div>
        <div class="duo-arrow">Voir les stats du duo →</div>
      </a>`;
    }).join('');
  }

  function renderLayout() {
    const status = (state.challenge && state.challenge.status) || 'registration';
    // Même règle que l'API (REGISTRATION_OPEN_STATUSES = registration + drawn) : les duos tirés
    // n'empêchent ni de s'inscrire ni de lier son compte tant que le challenge n'a pas démarré.
    const registration = status === 'registration' || status === 'drawn';
    els.registerCard.hidden = !registration;
    if (els.demoCard) els.demoCard.hidden = !registration;
    els.side.hidden = !registration;
    els.root.classList.toggle('home-layout', registration);
    els.playersHint.textContent = registration
      ? 'Chaque joueur lie son compte LoL pour être suivi.'
      : 'Les inscriptions sont closes.';
  }

  function render() {
    if (!state) return;
    renderHero();
    renderLayout();
    renderDuos();
    renderSlots();
  }

  /* ------------------------------------------------------------------ */
  async function load() {
    if (loading) return;
    loading = true;
    try {
      state = await api('/api/state');
      render();
    } catch (e) {
      if (!state) {
        els.slots.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-icon">⚠️</div><div class="empty-title">Impossible de charger les joueurs</div>${esc(e.message)}<br><button type="button" class="btn mt-sm" onclick="location.reload()">Réessayer</button></div>`;
      }
      toast(e.message, { type: 'error' });
    } finally {
      loading = false;
    }
  }

  async function onRegister(e) {
    e.preventDefault();
    els.formError.textContent = '';
    const display_name = els.form.display_name.value.trim();
    const riot_id = els.form.riot_id.value.trim();
    if (!display_name) { els.formError.textContent = 'Choisis un pseudo.'; els.form.display_name.focus(); return; }
    if (riot_id && !/^.{1,16}#.{2,5}$/.test(riot_id)) {
      els.formError.textContent = 'Le Riot ID a la forme Pseudo#TAG (ex. La Peace#CHILL).';
      els.form.riot_id.focus();
      return;
    }
    App.setLoading(els.submit, true);
    try {
      const res = await api('/api/players', { method: 'POST', body: { display_name, riot_id: riot_id || null } });
      const p = res && res.player;
      els.form.reset();
      if (p && riot_id && !p.is_linked) {
        toast(`Bienvenue ${display_name} ! Le compte n'a pas pu être lié : ${p.link_error || 'réessaie plus tard.'}`, { type: 'warning', timeout: 8000 });
      } else {
        toast(`Bienvenue ${display_name} !`, { type: 'success' });
      }
      await load();
    } catch (err) {
      els.formError.textContent = err.message;
    } finally {
      App.setLoading(els.submit, false);
    }
  }

  async function onLink(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const id = form.dataset.linkForm;
    const riot_id = form.riot_id.value.trim();
    const btn = form.querySelector('button');
    if (!riot_id || !riot_id.includes('#')) { toast('Le Riot ID a la forme Pseudo#TAG.', { type: 'error' }); form.riot_id.focus(); return; }
    App.setLoading(btn, true);
    try {
      const res = await api(`/api/players/${id}/link`, { method: 'POST', body: { riot_id } });
      const p = res && res.player;
      if (p && p.is_linked) toast(`Compte lié : ${p.riot_id}`, { type: 'success' });
      else toast((p && p.link_error) || "Le compte n'a pas pu être lié.", { type: 'error' });
      await load();
    } catch (err) {
      toast(err.message, { type: 'error' });
      App.setLoading(btn, false);
    }
  }

  async function onDemoFill() {
    App.setLoading(els.demoFill, true);
    try {
      await api('/api/demo/fill', { method: 'POST', body: {} });
      toast('Joueurs démo ajoutés.', { type: 'success' });
      await load();
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.demoFill, false);
    }
  }

  async function startChallenge(btn) {
    try {
      const c = state.challenge || {};
      const fmt = (iso) => new Date(iso).toLocaleString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
      const from = c.start_at ? (Date.parse(c.start_at) > Date.now() ? `à partir du ${fmt(c.start_at)}` : `depuis le ${fmt(c.start_at)}`) : 'à partir de maintenant';
      const until = c.end_at ? ` jusqu'au ${fmt(c.end_at)} (fin automatique)` : '';
      const res = await App.confirm({ title: 'Démarrer le challenge ?', message: `Les parties terminées ${from}${until} compteront pour le classement.`, confirmText: 'Démarrer' });
      if (!res.ok) return;
      App.setLoading(btn, true);
      // adminAction renvoie `undefined` si l'utilisateur annule la modale (api() renvoie null pour un corps vide)
      const r = await App.adminAction(() => api('/api/admin/challenge/start', { method: 'POST', admin: true, body: {} }));
      if (r === undefined) { App.setLoading(btn, false); return; }
      window.location.href = '/dashboard';
    } catch (err) {
      App.setLoading(btn, false);
      toast(err.message, { type: 'error' });
    }
  }

  /* ------------------------------------------------------------------ */
  els.form.addEventListener('submit', onRegister);
  if (els.demoFill) els.demoFill.addEventListener('click', onDemoFill);

  const reload = App.debounce(load, 600);
  App.connectEvents({
    player_registered: reload,
    player_linked: reload,
    draw_done: reload,
    challenge_started: reload,
    challenge_finished: reload,
    challenge_reset: reload,
    teams_changed: reload,
    rank_changed: reload,
  });
  setInterval(() => { if (!document.hidden) load(); }, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  load();
})();
