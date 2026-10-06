/* Administration : porte par mot de passe, puis actions sur le challenge, les duos, les joueurs, le système. */
(function () {
  'use strict';
  const App = window.App;
  const { $, $$, api, toast, escapeHtml: esc, avatar } = App;

  const els = {
    gate: $('#admin-gate'),
    gateForm: $('#gate-form'),
    gateError: $('#gate-error'),
    gateSubmit: $('#gate-submit'),
    panel: $('#admin-panel'),
    headActions: $('#admin-head-actions'),
    logout: $('#btn-logout'),
    status: $('#admin-status'),
    chForm: $('#challenge-form'),
    chSave: $('#ch-save'),
    btnStart: $('#btn-start'),
    btnFinish: $('#btn-finish'),
    btnReset: $('#btn-reset'),
    teams: $('#teams-admin'),
    players: $('#players-admin'),
    playersCount: $('#players-admin-count'),
    sysStats: $('#sys-stats'),
    sysErrors: $('#sys-errors'),
    sysMode: $('#sys-mode'),
    btnRefresh: $('#btn-refresh'),
    btnReload: $('#btn-reload'),
    btnDiscord: $('#btn-discord'),
  };

  let state = null;

  /* ------------------------------------------------------------------ */
  /* Dates : ISO UTC ↔ datetime-local                                     */
  /* ------------------------------------------------------------------ */
  function toLocalInput(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function fromLocalInput(value) {
    if (!value) return null;
    const d = new Date(value);
    return isNaN(d.getTime()) ? null : d.toISOString();
  }

  const admin = (fn) => App.adminAction(fn);

  /* ------------------------------------------------------------------ */
  /* Porte                                                                */
  /* ------------------------------------------------------------------ */
  async function unlock() {
    els.gate.hidden = true;
    els.panel.hidden = false;
    els.headActions.hidden = false;
    await load();
  }

  function lock() {
    App.setAdminPassword('');
    els.gate.hidden = false;
    els.panel.hidden = true;
    els.headActions.hidden = true;
    els.gateForm.password.value = '';
    els.gateForm.password.focus();
  }

  els.gateForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    els.gateError.textContent = '';
    const pw = els.gateForm.password.value;
    if (!pw) { els.gateError.textContent = 'Entre le mot de passe.'; return; }
    App.setLoading(els.gateSubmit, true);
    try {
      const ok = await App.verifyAdmin(pw);
      if (!ok) { els.gateError.textContent = 'Mot de passe incorrect.'; els.gateForm.password.select(); return; }
      await unlock();
    } catch (err) {
      els.gateError.textContent = err.message;
    } finally {
      App.setLoading(els.gateSubmit, false);
    }
  });
  els.logout.addEventListener('click', lock);

  /* ------------------------------------------------------------------ */
  /* Chargement                                                           */
  /* ------------------------------------------------------------------ */
  async function load() {
    try {
      state = await api('/api/state');
    } catch (e) {
      toast(e.message, { type: 'error' });
      return;
    }
    renderChallenge();
    renderTeams();
    renderPlayers();
    renderSystem(state.last_poll);
  }

  /* ------------------------------------------------------------------ */
  /* Challenge                                                            */
  /* ------------------------------------------------------------------ */
  function renderChallenge() {
    const c = state.challenge || {};
    els.status.innerHTML = App.statusChip(c.status);
    const f = els.chForm;
    if (document.activeElement && f.contains(document.activeElement)) return; // ne pas écraser une saisie en cours
    f.name.value = c.name || '';
    f.games_per_day.value = c.games_per_day || App.gamesPerDay;
    f.start_at.value = toLocalInput(c.start_at);
    f.end_at.value = toLocalInput(c.end_at);
    f.track_flex.checked = !!c.track_flex;
    els.btnStart.disabled = !(c.status === 'drawn' || c.status === 'registration');
    els.btnStart.title = c.status === 'registration' ? 'Tire d\'abord les duos (la roue)' : '';
    els.btnFinish.disabled = c.status !== 'running';
  }

  els.chForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = els.chForm;
    const body = {
      name: f.name.value.trim() || undefined,
      games_per_day: parseInt(f.games_per_day.value, 10) || undefined,
      start_at: fromLocalInput(f.start_at.value),
      end_at: fromLocalInput(f.end_at.value),
      track_flex: f.track_flex.checked,
    };
    App.setLoading(els.chSave, true);
    try {
      const r = await admin(() => api('/api/admin/challenge', { method: 'PATCH', admin: true, body }));
      if (r !== undefined) toast('Challenge enregistré.', { type: 'success' });
      f.name.blur();
      await load();
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.chSave, false);
    }
  });

  els.btnStart.addEventListener('click', async () => {
    const res = await App.confirm({ title: 'Démarrer le challenge ?', message: 'Le suivi des parties commence maintenant. Les joueurs seront prévenus.', confirmText: '🚀 Démarrer' });
    if (!res.ok) return;
    App.setLoading(els.btnStart, true);
    try {
      const r = await admin(() => api('/api/admin/challenge/start', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined) toast('Challenge démarré !', { type: 'success' });
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnStart, false);
      await load();
    }
  });

  els.btnFinish.addEventListener('click', async () => {
    const res = await App.confirm({ title: 'Terminer le challenge ?', message: 'Le classement sera figé à cet instant.', confirmText: '🏁 Terminer', danger: true });
    if (!res.ok) return;
    App.setLoading(els.btnFinish, true);
    try {
      const r = await admin(() => api('/api/admin/challenge/finish', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined) toast('Challenge terminé.', { type: 'success' });
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnFinish, false);
      await load();
    }
  });

  els.btnReset.addEventListener('click', async () => {
    const res = await App.confirm({
      title: 'Réinitialiser le challenge ?',
      message: 'Supprime les duos, les snapshots de rang et les parties enregistrées. Le challenge revient aux inscriptions.',
      extraHtml: '<label class="check mt-sm"><input type="checkbox" name="keep_players" checked> Garder les joueurs inscrits</label>',
      confirmText: 'Réinitialiser',
      danger: true,
    });
    if (!res.ok) return;
    App.setLoading(els.btnReset, true);
    try {
      const r = await admin(() => api('/api/admin/challenge/reset', { method: 'POST', admin: true, body: { keep_players: !!res.form.keep_players } }));
      if (r !== undefined) toast('Challenge réinitialisé.', { type: 'success' });
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnReset, false);
      await load();
    }
  });

  /* ------------------------------------------------------------------ */
  /* Duos                                                                 */
  /* ------------------------------------------------------------------ */
  function playerName(id) {
    const p = (state.players || []).find((x) => x.id === id);
    return p ? p.display_name : `#${id}`;
  }

  function renderTeams() {
    const teams = (state.teams || []).slice().sort((a, b) => (a.slot || 0) - (b.slot || 0));
    if (!teams.length) {
      els.teams.innerHTML = '<div class="empty"><div class="empty-icon">🎡</div><div class="empty-title">Aucun duo</div>Lance la roue pour tirer les duos.<br><a class="btn btn-primary" href="/wheel">La roue</a></div>';
      return;
    }
    els.teams.innerHTML = teams.map((t) => `<form class="team-admin" data-team="${t.id}">
      <div class="field"><label>&nbsp;</label><input type="color" name="color" value="${esc(t.color || '#e5b64d')}" aria-label="Couleur de ${esc(t.name)}"></div>
      <div class="field"><label>Nom <span class="muted">· ${(t.player_ids || []).map((id) => esc(playerName(id))).join(' & ')}</span></label><input type="text" name="name" value="${esc(t.name)}" maxlength="40"></div>
      <div class="field"><label>Fenêtre début <span class="muted">(optionnel)</span></label><input type="datetime-local" name="window_start" value="${toLocalInput(t.window_start)}"></div>
      <div class="field"><label>Fenêtre fin <span class="muted">(optionnel)</span></label><input type="datetime-local" name="window_end" value="${toLocalInput(t.window_end)}"></div>
      <button type="submit" class="btn btn-sm">Enregistrer</button>
    </form>`).join('');
    $$('form.team-admin', els.teams).forEach((form) => form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const id = form.dataset.team;
      const btn = form.querySelector('button');
      const body = {
        name: form.name.value.trim() || undefined,
        color: form.color.value,
        window_start: fromLocalInput(form.window_start.value),
        window_end: fromLocalInput(form.window_end.value),
      };
      App.setLoading(btn, true);
      try {
        const r = await admin(() => api(`/api/admin/teams/${id}`, { method: 'PATCH', admin: true, body }));
        if (r !== undefined) toast('Duo enregistré.', { type: 'success' });
        await load();
      } catch (err) {
        toast(err.message, { type: 'error' });
      } finally {
        App.setLoading(btn, false);
      }
    }));
  }

  /* ------------------------------------------------------------------ */
  /* Joueurs                                                              */
  /* ------------------------------------------------------------------ */
  function renderPlayers() {
    const players = (state.players || []).slice().sort((a, b) => (a.id || 0) - (b.id || 0));
    const teamById = {};
    (state.teams || []).forEach((t) => { teamById[t.id] = t; });
    els.playersCount.textContent = `${players.length} ${players.length > 1 ? 'inscrits' : 'inscrit'} · ${players.filter((p) => p.is_linked).length} liés`;
    if (!players.length) {
      els.players.innerHTML = '<tr><td colspan="6"><div class="empty" style="border:0"><div class="empty-title">Aucun joueur inscrit</div><a class="btn" href="/">Page d\'inscription</a></div></td></tr>';
      return;
    }
    els.players.innerHTML = players.map((p) => {
      const team = p.team_id ? teamById[p.team_id] : null;
      return `<tr data-player="${p.id}" class="${p.active === false ? 'is-stale' : ''}">
        <td><div class="cell-player">${avatar({ name: p.display_name, src: p.icon_url, color: team ? team.color : (p.rank_color || '#6b7280'), size: 'sm' })}<a href="/player/${p.id}">${esc(p.display_name)}</a></div></td>
        <td>${p.riot_id ? esc(p.riot_id) : '<span class="muted">—</span>'}</td>
        <td>${p.is_linked ? '<span class="chip chip-green">✓ lié</span>' : `<span class="chip chip-gold" title="${esc(p.link_error || '')}">À lier${p.link_error ? ' ⚠' : ''}</span>`}</td>
        <td>${team ? `<span class="chip chip-team" style="--team-color:${esc(team.color)}"><span class="swatch"></span>${esc(team.name)}</span>` : '<span class="muted">—</span>'}</td>
        <td><label class="switch" title="${p.active === false ? 'Réactiver' : 'Désactiver (exclu du suivi et de la roue)'}"><input type="checkbox" data-toggle-active="${p.id}" ${p.active === false ? '' : 'checked'}><span class="track"></span></label></td>
        <td class="right"><button type="button" class="btn btn-sm btn-danger" data-delete="${p.id}">Supprimer</button></td>
      </tr>`;
    }).join('');

    $$('[data-toggle-active]', els.players).forEach((input) => input.addEventListener('change', async () => {
      const id = input.dataset.toggleActive;
      const active = input.checked;
      input.disabled = true;
      try {
        const r = await admin(() => api(`/api/admin/players/${id}`, { method: 'PATCH', admin: true, body: { active } }));
        if (r === undefined) input.checked = !active;
        else toast(active ? 'Joueur réactivé.' : 'Joueur désactivé.', { type: 'success', timeout: 3000 });
        await load();
      } catch (err) {
        input.checked = !active;
        toast(err.message, { type: 'error' });
      } finally {
        input.disabled = false;
      }
    }));

    $$('[data-delete]', els.players).forEach((btn) => btn.addEventListener('click', async () => {
      const id = parseInt(btn.dataset.delete, 10);
      const p = players.find((x) => x.id === id);
      const res = await App.confirm({ title: `Supprimer ${p ? p.display_name : 'ce joueur'} ?`, message: 'Ses snapshots et ses parties seront supprimés. Cette action est définitive.', confirmText: 'Supprimer', danger: true });
      if (!res.ok) return;
      App.setLoading(btn, true);
      try {
        const r = await admin(() => api(`/api/admin/players/${id}`, { method: 'DELETE', admin: true }));
        if (r !== undefined) toast('Joueur supprimé.', { type: 'success' });
        await load();
      } catch (err) {
        toast(err.message, { type: 'error' });
        App.setLoading(btn, false);
      }
    }));
  }

  /* ------------------------------------------------------------------ */
  /* Système                                                              */
  /* ------------------------------------------------------------------ */
  function stat(label, value, sub) {
    return `<div class="tile card card-flat"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ''}</div>`;
  }

  function renderSystem(report) {
    els.sysMode.textContent = (state && state.demo_mode) || App.demoMode ? 'Mode démo (API simulée)' : 'API Riot';
    if (!report) {
      els.sysStats.innerHTML = stat('Dernier cycle', '—', 'aucun cycle pour l\'instant') + stat('Durée', '—') + stat('Requêtes Riot', '—') + stat('Erreurs', '0');
      els.sysErrors.hidden = true;
      return;
    }
    const errors = report.errors || [];
    els.sysStats.innerHTML =
      stat('Dernier cycle', esc(App.timeAgo(report.started_at)), esc(App.formatDateTime(report.started_at))) +
      stat('Durée', `${Number(report.duration_s || 0).toFixed(2)} s`, `${report.players_polled || 0} joueurs interrogés`) +
      stat('Requêtes Riot', `${report.requests || 0}`, `${report.new_matches || 0} parties · ${report.new_snapshots || 0} snapshots`) +
      stat('Erreurs', `<span class="${errors.length ? 'lp-neg' : 'lp-pos'}">${errors.length}</span>`);
    els.sysErrors.hidden = !errors.length;
    els.sysErrors.innerHTML = errors.map((e) => esc(e)).join('<br>');
  }

  els.btnRefresh.addEventListener('click', async () => {
    App.setLoading(els.btnRefresh, true);
    try {
      const report = await admin(() => api('/api/admin/refresh', { method: 'POST', admin: true, body: {} }));
      if (report !== undefined) {
        renderSystem(report && report.started_at ? report : (report && report.report) || report);
        toast('Cycle de rafraîchissement terminé.', { type: 'success' });
        await load();
      }
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnRefresh, false);
    }
  });

  els.btnReload.addEventListener('click', async () => {
    App.setLoading(els.btnReload, true);
    try {
      const r = await admin(() => api('/api/admin/reload-settings', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined) {
        toast(`.env rechargé — ${r && r.demo_mode ? 'mode démo' : 'API Riot'}${r && r.has_api_key ? ', clé présente' : ', pas de clé'}.`, { type: 'success' });
        if (r && typeof r.demo_mode === 'boolean' && r.demo_mode !== App.demoMode) setTimeout(() => location.reload(), 800);
        await load();
      }
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnReload, false);
    }
  });

  els.btnDiscord.addEventListener('click', async () => {
    App.setLoading(els.btnDiscord, true);
    try {
      const r = await admin(() => api('/api/admin/test-notification', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined) {
        if (r && r.sent) toast('Message de test envoyé sur Discord.', { type: 'success' });
        else toast('Rien envoyé : DISCORD_WEBHOOK_URL est vide ou l\'envoi a échoué.', { type: 'warning' });
      }
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnDiscord, false);
    }
  });

  /* ------------------------------------------------------------------ */
  App.connectEvents({
    poll_done: App.debounce(() => { if (!els.panel.hidden) load(); }, 1000),
    draw_done: () => { if (!els.panel.hidden) load(); },
    challenge_started: () => { if (!els.panel.hidden) load(); },
    challenge_finished: () => { if (!els.panel.hidden) load(); },
    challenge_reset: () => { if (!els.panel.hidden) load(); },
    player_registered: () => { if (!els.panel.hidden) load(); },
    player_linked: () => { if (!els.panel.hidden) load(); },
  });

  (async () => {
    const stored = App.getAdminPassword();
    if (stored) {
      try {
        if (await App.verifyAdmin(stored)) { await unlock(); return; }
      } catch (e) { /* serveur injoignable : on laisse la porte */ }
      App.setAdminPassword('');
    }
    els.gateForm.password.focus();
  })();
})();
