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
    teamsNotice: $('#teams-admin-notice'),
    teamsHelp: $('#teams-admin-help'),
    teamsActions: $('#teams-admin-actions'),
    teamsUnassigned: $('#teams-unassigned'),
    btnTeamAdd: $('#btn-team-add'),
    btnTeamAuto: $('#btn-team-auto'),
    players: $('#players-admin'),
    playersCount: $('#players-admin-count'),
    sysStats: $('#sys-stats'),
    sysErrors: $('#sys-errors'),
    sysMode: $('#sys-mode'),
    sysUrl: $('#sys-url'),
    btnRefresh: $('#btn-refresh'),
    btnLiveCheck: $('#btn-live-check'),
    liveCheck: $('#live-check'),
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
    renderDemoPlayersNotice();
  }

  /* Joueurs créés en mode démo (identifiants inventés) alors que le site est en mode réel :
     Riot les refuse à chaque cycle → on propose de les supprimer en un clic. */
  function renderDemoPlayersNotice() {
    const box = document.getElementById('demo-players-notice');
    if (!box) return;
    const ids = (state && state.demo_players) || [];
    const isReal = state && state.demo_mode === false;
    if (!isReal || !ids.length) { box.hidden = true; box.innerHTML = ''; return; }
    const names = (state.players || []).filter((p) => ids.includes(p.id)).map((p) => p.display_name);
    box.hidden = false;
    box.innerHTML = `⚠️ <strong>${ids.length} joueur${ids.length > 1 ? 's' : ''} de démo</strong> (${esc(names.join(', '))})
      ${ids.length > 1 ? 'sont encore inscrits' : 'est encore inscrit'} alors que le site est branché sur la vraie API Riot :
      leurs identifiants sont inventés, Riot les refuse et ils bloquent le suivi.
      <button type="button" class="btn btn-sm btn-primary" id="btn-remove-demo">Supprimer les joueurs de démo</button>`;
    const btn = document.getElementById('btn-remove-demo');
    if (btn) btn.addEventListener('click', async () => {
      const res = await App.confirm({ title: 'Supprimer les joueurs de démo ?', message: `${names.join(', ')} et leurs parties simulées seront supprimés. Les vrais joueurs ne sont pas touchés.`, confirmText: 'Supprimer', danger: true });
      if (!res || !res.ok) return;
      App.setLoading(btn, true);
      try {
        const r = await admin(() => api('/api/admin/players/demo/all', { method: 'DELETE', admin: true }));
        if (r !== undefined) {
          const duos = r.teams_removed ? ` · ${r.teams_removed} duo(s) vide(s) retiré(s)` : '';
          toast(`${r.deleted} joueur(s) de démo supprimé(s)${duos}.`, { type: 'success' });
          // Challenge en cours / terminé : les duos sont figés, on explique quoi faire
          if (r.hint) toast(r.hint, { type: 'warning', timeout: 12000 });
          await load();
        }
      } catch (err) {
        toast(err.message, { type: 'error' });
      } finally {
        App.setLoading(btn, false);
      }
    });
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
    els.btnStart.title = c.status === 'registration' ? 'Tous les joueurs actifs et liés doivent être dans un duo complet' : '';
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
      if (r !== undefined) {
        toast('Challenge démarré !', { type: 'success' });
        (r.warnings || []).forEach((w) => toast(`⚠️ ${w}`, { type: 'warning', timeout: 9000 }));
      }
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
  /* Duos : éditeur manuel (nom, couleur, deux joueurs, fenêtre)          */
  /* ------------------------------------------------------------------ */
  function playerName(id) {
    const p = (state.players || []).find((x) => x.id === id);
    return p ? p.display_name : `#${id}`;
  }

  /* Le challenge figé (en cours / terminé) : les duos ne se modifient plus. */
  function teamsReadOnly() {
    const status = (state.challenge && state.challenge.status) || 'registration';
    return status === 'running' || status === 'finished';
  }

  function playerOptions(team, selectedId) {
    const teamById = {};
    (state.teams || []).forEach((t) => { teamById[t.id] = t; });
    const players = (state.players || []).filter((p) => p.active !== false).slice().sort((a, b) => a.display_name.localeCompare(b.display_name, 'fr'));
    let html = `<option value="" ${selectedId ? '' : 'selected'}>— Personne —</option>`;
    players.forEach((p) => {
      const elsewhere = p.team_id && p.team_id !== team.id ? teamById[p.team_id] : null;
      const label = `${p.display_name}${elsewhere ? ` (${elsewhere.name})` : ''}${p.is_linked ? '' : ' · compte à lier'}`;
      html += `<option value="${p.id}" ${p.id === selectedId ? 'selected' : ''} class="${elsewhere ? 'is-elsewhere' : ''}">${esc(label)}</option>`;
    });
    return html;
  }

  function renderTeams() {
    const teams = (state.teams || []).slice().sort((a, b) => (a.slot || 0) - (b.slot || 0));
    const readOnly = teamsReadOnly();
    const status = (state.challenge && state.challenge.status) || 'registration';

    els.teamsNotice.hidden = !readOnly;
    els.teamsNotice.textContent = status === 'running'
      ? '🔒 Le challenge est en cours : les duos sont figés. Termine ou réinitialise le challenge pour les modifier.'
      : '🔒 Le challenge est terminé : les duos sont figés. Clique « Réinitialiser » (en gardant les joueurs inscrits) pour recomposer les duos et rouvrir les inscriptions.';
    els.teamsHelp.hidden = readOnly;
    els.teamsActions.hidden = readOnly;
    // Duos tirés puis vidés (joueurs supprimés un à un, duos supprimés…) : le statut reste « Duos formés »
    // et rien ne dit que l'accueil accepte encore les inscriptions ni que « Réinitialiser » rouvre tout.
    if (!readOnly && status === 'drawn' && !(state.players || []).some((p) => p.active !== false)) {
      els.teamsNotice.hidden = false;
      els.teamsNotice.textContent = 'Aucun joueur inscrit : les inscriptions restent ouvertes sur l\'accueil, ou clique sur « Réinitialiser » pour repartir des inscriptions.';
    }

    if (!teams.length) {
      els.teams.innerHTML = readOnly
        ? '<div class="empty"><div class="empty-title">Aucun duo</div></div>'
        : '<div class="empty"><div class="empty-icon">🤝</div><div class="empty-title">Aucun duo pour l\'instant</div>Ajoute un duo et choisis ses deux joueurs, ou forme-les au hasard.</div>';
    } else {
      els.teams.innerHTML = teams.map((t, i) => {
        const ids = t.player_ids || [];
        const hasWindow = !!(t.window_start || t.window_end);
        const dis = readOnly ? 'disabled' : '';
        return `<form class="team-admin ${readOnly ? 'is-readonly' : ''}" data-team="${t.id}" style="--team-color:${esc(t.color || '#e5b64d')}" autocomplete="off">
          <div class="ta-main">
            <div class="field ta-color"><label for="ta-color-${t.id}" class="sr-only">Couleur du duo</label><input id="ta-color-${t.id}" type="color" name="color" value="${esc(t.color || '#e5b64d')}" title="Couleur du duo" ${dis}></div>
            <div class="field"><label for="ta-name-${t.id}">Nom du duo ${i + 1}</label><input id="ta-name-${t.id}" type="text" name="name" value="${esc(t.name)}" maxlength="40" placeholder="Duo ${i + 1}" ${dis}></div>
            <div class="field"><label for="ta-p1-${t.id}">Joueur 1</label><select id="ta-p1-${t.id}" name="p1" ${dis}>${playerOptions(t, ids[0])}</select></div>
            <div class="field"><label for="ta-p2-${t.id}">Joueur 2</label><select id="ta-p2-${t.id}" name="p2" ${dis}>${playerOptions(t, ids[1])}</select></div>
          </div>
          <details class="ta-window" ${hasWindow ? 'open' : ''}>
            <summary>Fenêtre de dates personnalisée <span class="muted">(optionnel · sinon celle du challenge)</span></summary>
            <div class="form-row">
              <div class="field"><label for="ta-ws-${t.id}">Début</label><input id="ta-ws-${t.id}" type="datetime-local" name="window_start" value="${toLocalInput(t.window_start)}" ${dis}></div>
              <div class="field"><label for="ta-we-${t.id}">Fin</label><input id="ta-we-${t.id}" type="datetime-local" name="window_end" value="${toLocalInput(t.window_end)}" ${dis}></div>
            </div>
          </details>
          ${readOnly ? '' : `<div class="ta-actions">
            <button type="submit" class="btn btn-sm btn-primary">Enregistrer</button>
            <button type="button" class="btn btn-sm btn-danger" data-delete-team="${t.id}">Supprimer</button>
            <span class="ta-meta">${ids.length ? ids.map((id) => esc(playerName(id))).join(' & ') : 'Aucun joueur'}${ids.length < 2 ? ' · duo incomplet' : ''}</span>
          </div>`}
        </form>`;
      }).join('');
    }

    renderUnassigned();
    if (readOnly) return;

    $$('form.team-admin', els.teams).forEach((form) => {
      form.color.addEventListener('input', () => { form.style.setProperty('--team-color', form.color.value); });
      form.addEventListener('submit', (e) => { e.preventDefault(); saveTeam(form); });
    });
    $$('[data-delete-team]', els.teams).forEach((btn) => btn.addEventListener('click', () => deleteTeam(parseInt(btn.dataset.deleteTeam, 10), btn)));
  }

  function renderUnassigned() {
    const assigned = new Set();
    (state.teams || []).forEach((t) => (t.player_ids || []).forEach((id) => assigned.add(id)));
    const list = (state.players || []).filter((p) => p.active !== false && !assigned.has(p.id) && !p.team_id);
    els.teamsUnassigned.hidden = !list.length;
    if (!list.length) return;
    els.teamsUnassigned.innerHTML = `<span class="lbl">Sans duo (${list.length}) :</span>` + list.map((p) =>
      `<span class="chip ${p.is_linked ? '' : 'chip-gold'}" title="${p.is_linked ? 'Compte lié' : 'Compte à lier'}">${esc(p.display_name)}${p.is_linked ? '' : ' · à lier'}</span>`).join('');
  }

  async function saveTeam(form) {
    const id = form.dataset.team;
    const btn = form.querySelector('button[type="submit"]');
    const p1 = parseInt(form.p1.value, 10) || null;
    const p2 = parseInt(form.p2.value, 10) || null;
    if (p1 && p2 && p1 === p2) { toast('Choisis deux joueurs différents.', { type: 'error' }); form.p2.focus(); return; }
    const body = {
      name: form.name.value.trim() || undefined,
      color: form.color.value,
      window_start: fromLocalInput(form.window_start.value),
      window_end: fromLocalInput(form.window_end.value),
      player_ids: [p1, p2].filter(Boolean),
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
  }

  async function deleteTeam(id, btn) {
    const t = (state.teams || []).find((x) => x.id === id);
    const res = await App.confirm({ title: `Supprimer ${t ? t.name : 'ce duo'} ?`, message: 'Ses joueurs se retrouvent sans duo. Les parties déjà enregistrées sont conservées.', confirmText: 'Supprimer', danger: true });
    if (!res.ok) return;
    App.setLoading(btn, true);
    try {
      const r = await admin(() => api(`/api/admin/teams/${id}`, { method: 'DELETE', admin: true }));
      if (r !== undefined) toast('Duo supprimé.', { type: 'success' });
      await load();
    } catch (err) {
      toast(err.message, { type: 'error' });
      App.setLoading(btn, false);
    }
  }

  els.btnTeamAdd.addEventListener('click', async () => {
    App.setLoading(els.btnTeamAdd, true);
    try {
      const r = await admin(() => api('/api/admin/teams', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined) {
        toast('Duo ajouté : choisis ses deux joueurs puis enregistre.', { type: 'success' });
        await load();
        const created = r && r.team && r.team.id;
        const input = created ? $(`form.team-admin[data-team="${created}"] input[name="name"]`) : null;
        if (input) { input.focus(); input.select(); input.closest('form').scrollIntoView({ block: 'nearest' }); }
      }
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnTeamAdd, false);
    }
  });

  els.btnTeamAuto.addEventListener('click', async () => {
    const hasTeams = (state.teams || []).length > 0;
    const res = await App.confirm({
      title: 'Former les duos au hasard ?',
      message: `${hasTeams ? 'Les duos actuels seront remplacés. ' : ''}Les joueurs actifs et liés sont mélangés puis regroupés deux par deux.`,
      confirmText: '🎲 Former les duos',
      danger: hasTeams,
    });
    if (!res.ok) return;
    App.setLoading(els.btnTeamAuto, true);
    try {
      const r = await admin(() => api('/api/admin/teams/auto', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined) {
        const n = (r && r.teams && r.teams.length) || 0;
        toast(n ? `${n} duo${n > 1 ? 's' : ''} formé${n > 1 ? 's' : ''} au hasard.` : 'Les duos ont été formés au hasard.', { type: 'success' });
      }
      await load();
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnTeamAuto, false);
    }
  });

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
        <td><label class="switch" title="${p.active === false ? 'Réactiver' : 'Désactiver (exclu du suivi et des duos)'}"><input type="checkbox" data-toggle-active="${p.id}" ${p.active === false ? '' : 'checked'}><span class="track"></span></label></td>
        <td class="right"><button type="button" class="btn btn-sm btn-danger" data-delete="${p.id}">Supprimer</button></td>
      </tr>`;
    }).join('');

    $$('[data-toggle-active]', els.players).forEach((input) => input.addEventListener('change', async () => {
      const id = input.dataset.toggleActive;
      const active = input.checked;
      // Désactiver retire aussi le joueur de son duo (côté serveur) : on le dit dans le toast
      const before = (state.players || []).find((p) => String(p.id) === id);
      const hadDuo = !active && !!(before && before.team_id);
      input.disabled = true;
      try {
        const r = await admin(() => api(`/api/admin/players/${id}`, { method: 'PATCH', admin: true, body: { active } }));
        if (r === undefined) input.checked = !active;
        else if (active) toast('Joueur réactivé.', { type: 'success', timeout: 3000 });
        else if (!hadDuo) toast('Joueur désactivé.', { type: 'success', timeout: 3000 });
        else if (teamsReadOnly()) toast('Joueur désactivé et retiré de son duo. Les duos étant figés, le réactiver ne l\'y remettra pas avant une réinitialisation.', { type: 'warning', timeout: 8000 });
        else toast('Joueur désactivé et retiré de son duo.', { type: 'success', timeout: 4000 });
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

  function renderPublicUrl() {
    if (!els.sysUrl) return;
    const pub = (state && state.public_url) || null;
    const url = (pub && pub.url) || (state && state.base_url) || '';
    const source = pub ? pub.source : 'local';
    let help;
    if (source === 'tunnel') {
      help = `Adresse du tunnel Cloudflare détectée automatiquement${pub.detected_at ? ` (${esc(App.timeAgo(pub.detected_at))})` : ''} : c'est celle à partager aux joueurs. Elle change à chaque relance du tunnel. Les messages Discord l'utilisent.`;
    } else if (source === 'env') {
      help = 'Adresse définie par BASE_URL dans .env : c\'est celle à partager aux joueurs (utilisée dans les messages Discord).';
    } else {
      help = 'Adresse locale : seul ce PC y accède. Lance Tunnel.bat (ou laisse PekinExpress.bat le faire) pour obtenir une adresse https://….trycloudflare.com à partager ; elle s\'affichera ici automatiquement.';
    }
    els.sysUrl.innerHTML = `
      <span class="section-label">Adresse du site</span>
      <div class="sys-url-row">
        <code class="sys-url-value">${esc(url || '—')}</code>
        <button type="button" class="btn btn-sm" id="btn-copy-url">Copier</button>
        ${source === 'tunnel' ? '<span class="chip chip-success">tunnel actif</span>' : ''}
      </div>
      <div class="muted small">${help}</div>`;
    const btn = $('#btn-copy-url');
    if (btn) btn.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(url); toast('Adresse copiée.', { type: 'success' }); }
      catch (e) { toast('Copie impossible : sélectionne l\'adresse à la main.', { type: 'error' }); }
    });
  }

  function renderSystem(report) {
    els.sysMode.textContent = (state && state.demo_mode) || App.demoMode ? 'Mode démo (API simulée)' : 'API Riot';
    renderPublicUrl();
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

  function renderLiveCheck(r) {
    const players = (r && r.players) || [];
    const when = r && r.checked_at ? App.formatTime(r.checked_at) : '';
    const rows = players.map((p) => {
      let status;
      if (p.error) status = `<span class="lp-neg">✗ ${esc(p.error)}</span>`;
      else if (p.in_game) {
        const kind = p.ranked ? 'classée' : (p.game_mode ? esc(p.game_mode.toLowerCase()) : 'non classée');
        status = `<span class="badge-live"><span class="dot"></span>En game${p.champion_name ? ` · ${esc(p.champion_name)}` : ''} (${kind})${p.elapsed_s ? ` · ${App.formatDuration(p.elapsed_s)}` : ''}</span>`;
      } else status = '<span class="muted">Pas en partie</span>';
      return `<li class="flex between wrap" style="gap:8px;padding:6px 0;border-bottom:1px solid var(--border, rgba(255,255,255,.08))"><span><strong>${esc(p.display_name)}</strong> <span class="muted">${esc(p.riot_id || '')}</span></span>${status}</li>`;
    }).join('');
    const empty = players.length ? '' : '<p class="muted">Aucun joueur lié à vérifier (actif, compte Riot lié).</p>';
    const every = r && r.live_poll_seconds && r.poll_interval_seconds && r.live_poll_seconds < r.poll_interval_seconds
      ? `vérification automatique toutes les ${r.live_poll_seconds} s`
      : `vérification automatique toutes les ${(r && r.poll_interval_seconds) || 90} s`;
    els.liveCheck.innerHTML = `<div class="muted" style="font-size:.85em">Vérifié à ${esc(when)} · ${esc(every)}${r && r.demo_mode ? ' · mode démo (parties simulées)' : ''}</div><ul style="list-style:none;margin:6px 0 0;padding:0">${rows}</ul>${empty}`;
    els.liveCheck.hidden = false;
  }

  els.btnLiveCheck.addEventListener('click', async () => {
    App.setLoading(els.btnLiveCheck, true);
    try {
      const r = await admin(() => api('/api/admin/live-check', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined) {
        renderLiveCheck(r);
        const n = (r.players || []).filter((p) => p.in_game).length;
        toast(n ? `${n} joueur${n > 1 ? 's' : ''} en game.` : 'Personne en game pour Riot en ce moment.', { type: n ? 'success' : 'info' });
      }
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnLiveCheck, false);
    }
  });

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
    teams_changed: () => { if (!els.panel.hidden) load(); },
    team_updated: () => { if (!els.panel.hidden) load(); },
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
