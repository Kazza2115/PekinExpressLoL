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
    btnAnnounce: $('#btn-announce'),
  };

  let state = null;

  /* ------------------------------------------------------------------ */
  /* Dates : ISO UTC ↔ « jj/mm/aaaa hh:mm », toujours à l'heure de Paris (fuseau du challenge),
     quel que soit le réglage de date du navigateur.                     */
  /* ------------------------------------------------------------------ */
  const PARIS = 'Europe/Paris';
  const pad2 = (n) => String(n).padStart(2, '0');
  function parisParts(ms) {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: PARIS, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms));
    const get = (type) => parseInt((parts.find((x) => x.type === type) || {}).value, 10);
    return { y: get('year'), m: get('month'), d: get('day'), h: get('hour') % 24, mi: get('minute') };
  }
  function parisWallToMs(y, m, d, h, mi) {
    const guess = Date.UTC(y, m - 1, d, h, mi);
    const p = parisParts(guess);
    return guess - (Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi) - guess);
  }
  function toLocalInput(iso) {
    if (!iso) return '';
    const ms = Date.parse(iso);
    if (isNaN(ms)) return '';
    const p = parisParts(ms);
    return `${pad2(p.d)}/${pad2(p.m)}/${p.y} ${pad2(p.h)}:${pad2(p.mi)}`;
  }
  /* « 10/10/2026 09:00 » (ou « 10/10/2026 9h », « 12/10/2026 ») → ISO ; vide → null ; invalide → undefined. */
  const FR_DATE_RE = /^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?:\s*(?:à\s*)?(\d{1,2})\s*[:hH]\s*(\d{2})?)?\s*$/;
  function fromLocalInput(value) {
    if (!value || !value.trim()) return null;
    const m = FR_DATE_RE.exec(value);
    if (!m) return undefined;
    const [d, mo, y, h, mi] = [m[1], m[2], m[3], m[4] || 0, m[5] || 0].map((x) => parseInt(x, 10));
    const check = new Date(Date.UTC(y, mo - 1, d));
    if (check.getUTCDate() !== d || check.getUTCMonth() !== mo - 1 || h > 23 || mi > 59) return undefined;
    return new Date(parisWallToMs(y, mo, d, h, mi)).toISOString();
  }
  /* Dates d'un formulaire : null si l'une est mal écrite (message affiché, champ sélectionné). */
  function readDates(fields) {
    const out = {};
    for (const [key, input] of fields) {
      const value = fromLocalInput(input.value);
      if (value === undefined) {
        toast('Date invalide : écris-la au format jj/mm/aaaa hh:mm (ex. 10/10/2026 09:00).', { type: 'error', timeout: 8000 });
        input.focus();
        return null;
      }
      out[key] = value;
    }
    return out;
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
  /* Champs du challenge modifiés et pas encore enregistrés : un rechargement (événement en direct)
     ne doit pas les effacer. Vidé après « Enregistrer » ou « Démarrer ». */
  const chDirty = new Set();
  ['name', 'games_per_day', 'start_at', 'end_at', 'track_flex', 'jokers_per_team', 'joker_extra_games'].forEach((name) => {
    const input = els.chForm.elements[name];
    if (!input) return;
    input.addEventListener('input', () => chDirty.add(name));
    input.addEventListener('change', () => chDirty.add(name));
  });

  function renderChallenge() {
    const c = state.challenge || {};
    els.status.innerHTML = App.statusChip(c.status, c.start_at);
    els.btnStart.disabled = !(c.status === 'drawn' || c.status === 'registration');
    els.btnStart.title = c.status === 'registration' ? 'Tous les joueurs actifs et liés doivent être dans un duo complet' : '';
    els.btnFinish.disabled = c.status !== 'running';
    const f = els.chForm;
    const set = (name, apply) => { if (!chDirty.has(name) && f.elements[name]) apply(f.elements[name]); };
    set('name', (i) => { i.value = c.name || ''; });
    set('games_per_day', (i) => { i.value = c.games_per_day || App.gamesPerDay; });
    set('start_at', (i) => { i.value = toLocalInput(c.start_at); });
    set('end_at', (i) => { i.value = toLocalInput(c.end_at); });
    set('track_flex', (i) => { i.checked = !!c.track_flex; });
    set('jokers_per_team', (i) => { i.value = c.jokers_per_team ?? 1; });
    set('joker_extra_games', (i) => { i.value = c.joker_extra_games ?? 3; });
  }

  /* Champs modifiés autres que les dates (envoyées avec « Démarrer »). */
  function dirtyChallengeFields() {
    const f = els.chForm;
    const body = {};
    if (chDirty.has('name') && f.name.value.trim()) body.name = f.name.value.trim();
    if (chDirty.has('games_per_day') && parseInt(f.games_per_day.value, 10)) body.games_per_day = parseInt(f.games_per_day.value, 10);
    if (chDirty.has('track_flex')) body.track_flex = f.track_flex.checked;
    ['jokers_per_team', 'joker_extra_games'].forEach((name) => {
      const value = parseInt(f.elements[name] && f.elements[name].value, 10);
      if (chDirty.has(name) && !isNaN(value)) body[name] = value;
    });
    return body;
  }

  els.chForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = els.chForm;
    const body = {
      name: f.name.value.trim() || undefined,
      games_per_day: parseInt(f.games_per_day.value, 10) || undefined,
      track_flex: f.track_flex.checked,
    };
    // Dates envoyées seulement si elles ont été modifiées (sinon l'arrondi à la minute du champ
    // déplacerait un début ou une fin enregistrés à la seconde près)
    const dates = readDates([['start_at', f.start_at], ['end_at', f.end_at]]);
    if (!dates) return;
    if (chDirty.has('start_at')) body.start_at = dates.start_at;
    if (chDirty.has('end_at')) body.end_at = dates.end_at;
    ['jokers_per_team', 'joker_extra_games'].forEach((name) => {
      const value = parseInt(f.elements[name] && f.elements[name].value, 10);
      if (!isNaN(value)) body[name] = value;
    });
    App.setLoading(els.chSave, true);
    try {
      const r = await admin(() => api('/api/admin/challenge', { method: 'PATCH', admin: true, body }));
      if (r !== undefined) {
        chDirty.clear();
        toast(r.reopened ? 'Challenge enregistré : il reprend jusqu\'à la nouvelle fin.' : 'Challenge enregistré.', { type: 'success' });
      }
      f.name.blur();
      await load();
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.chSave, false);
    }
  });

  const longDate = (iso) => new Date(iso).toLocaleString('fr-FR', { timeZone: PARIS, weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
  const sameInstant = (a, b) => (a ? Date.parse(a) : null) === (b ? Date.parse(b) : null);

  /* « jusqu'à minuit dans la nuit du dimanche 11 au lundi 12 octobre » pour une fin à 00:00. */
  function endPhrase(iso) {
    const d = new Date(iso);
    const wall = parisParts(d.getTime());
    if (wall.h === 0 && wall.mi === 0) {
      const before = new Date(d.getTime() - 60000);
      const day = (x, opts) => x.toLocaleDateString('fr-FR', { timeZone: PARIS, ...opts });
      return `jusqu'à minuit, dans la nuit du ${day(before, { weekday: 'long', day: 'numeric' })} au ${day(d, { weekday: 'long', day: 'numeric', month: 'long' })}`;
    }
    return `jusqu'au ${longDate(iso)}`;
  }

  els.btnStart.addEventListener('click', async () => {
    const c = state.challenge || {};
    if (!(c.status === 'registration' || c.status === 'drawn')) { await load(); return; } // déjà démarré
    const f = els.chForm;
    const dates = readDates([['start_at', f.start_at], ['end_at', f.end_at]]);
    if (!dates) return;
    const startAt = dates.start_at || new Date().toISOString();
    const endAt = dates.end_at;
    const future = Date.parse(startAt) > Date.now() + 60000;
    const startTxt = future ? `Les parties compteront à partir du ${longDate(startAt)}` : `Les parties comptent depuis le ${longDate(startAt)}`;
    const hours = endAt ? Math.round((Date.parse(endAt) - Date.parse(startAt)) / 3600000) : null;
    const endTxt = endAt ? ` ${endPhrase(endAt)} (fin automatique, durée ${hours} h)` : ' jusqu\'au clic sur « Terminer »';
    const warn = endAt && hours !== null && hours < 24 ? ' ⚠️ Moins de 24 h : vérifie la date de fin.' : '';
    const res = await App.confirm({
      title: 'Démarrer le challenge ?',
      message: `${startTxt}${endTxt}.${warn} Les inscriptions et la composition des duos seront figées. Les joueurs seront prévenus.`,
      confirmText: '🚀 Démarrer',
    });
    if (!res.ok) return;
    App.setLoading(els.btnStart, true);
    try {
      const others = dirtyChallengeFields();
      if (Object.keys(others).length) {
        const saved = await admin(() => api('/api/admin/challenge', { method: 'PATCH', admin: true, body: others }));
        if (saved === undefined) return;
      }
      // Les dates partent avec le démarrage : un challenge déjà en cours est refusé sans rien modifier
      const r = await admin(() => api('/api/admin/challenge/start', { method: 'POST', admin: true, body: { start_at: startAt, end_at: endAt } }));
      if (r !== undefined) {
        chDirty.clear();
        const started = r.challenge && r.challenge.start_at;
        toast(started && Date.parse(started) > Date.now() ? `Challenge prêt : les parties compteront à partir du ${longDate(started)}.` : 'Challenge démarré !', { type: 'success', timeout: 9000 });
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
    return status === 'finished'; // en cours : duos encore modifiables (joueur arrivé en retard)
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

    els.teamsNotice.hidden = !readOnly && status !== 'running';
    els.teamsNotice.textContent = status === 'running'
      ? '⚠️ Le challenge est en cours : tu peux encore changer les duos (joueur arrivé en retard : il s’inscrit sur l’accueil, puis tu le places ici). Les LP et parties d’un joueur le suivent dans son nouveau duo.'
      : '🔒 Le challenge est terminé : les duos sont figés. Clique « Réinitialiser » (en gardant les joueurs inscrits) pour recomposer les duos et rouvrir les inscriptions.';
    els.teamsHelp.hidden = readOnly;
    els.teamsActions.hidden = readOnly;
    // Tirage au hasard : seulement avant le départ (il remplace tous les duos, jokers compris)
    const autoBtn = $('#btn-team-auto');
    if (autoBtn) autoBtn.hidden = status === 'running';
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
              <div class="field"><label for="ta-ws-${t.id}">Début</label><input id="ta-ws-${t.id}" type="text" placeholder="jj/mm/aaaa hh:mm" autocomplete="off" name="window_start" value="${toLocalInput(t.window_start)}" ${dis}></div>
              <div class="field"><label for="ta-we-${t.id}">Fin</label><input id="ta-we-${t.id}" type="text" placeholder="jj/mm/aaaa hh:mm" autocomplete="off" name="window_end" value="${toLocalInput(t.window_end)}" ${dis}></div>
            </div>
          </details>
          ${jokersOfTeamHtml(t.id)}
          ${readOnly ? '' : `<div class="ta-actions">
            <button type="submit" class="btn btn-sm btn-primary">Enregistrer</button>
            <button type="button" class="btn btn-sm btn-danger" data-delete-team="${t.id}">Supprimer</button>
            <span class="ta-meta">${ids.length ? ids.map((id) => esc(playerName(id))).join(' & ') : 'Aucun joueur'}${ids.length < 2 ? ' · duo incomplet' : ''}</span>
          </div>`}
        </form>`;
      }).join('');
    }

    renderUnassigned();
    // Annuler un joker : possible pendant le challenge, quand les duos sont figés
    $$('[data-cancel-joker]', els.teams).forEach((btn) => btn.addEventListener('click', () => cancelJoker(parseInt(btn.dataset.cancelJoker, 10), btn)));
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
      player_ids: [p1, p2].filter(Boolean),
    };
    const windowDates = readDates([['window_start', form.window_start], ['window_end', form.window_end]]);
    if (!windowDates) return;
    body.window_start = windowDates.window_start;
    body.window_end = windowDates.window_end;
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

  /* Lien fixe GitHub Pages : redirige vers l'adresse actuelle du site, publiée par le serveur. */
  function portalHtml(portal) {
    if (!portal || !portal.portal_url) return '';
    if (!portal.enabled) {
      return `<div class="muted small mt-sm">Lien fixe gratuit : ajoute <code>GITHUB_TOKEN</code> dans .env et active GitHub Pages (README, « Lien fixe avec Cloudflare »). Ce lien ne changera plus : <code>${esc(portal.portal_url)}</code></div>`;
    }
    let status;
    if (portal.error) status = `<span class="lp-neg">✗ ${esc(portal.error)}</span>`;
    else if (portal.waiting) status = `⏳ ${esc(portal.waiting)}${portal.published_url ? ` Pour l'instant, il renvoie encore vers <code>${esc(portal.published_url)}</code>.` : ''}`;
    else if (portal.published_url) status = `Renvoie vers <code>${esc(portal.published_url)}</code>${portal.published_at ? ` (mis à jour ${esc(App.timeAgo(portal.published_at))})` : ''}.`;
    else status = 'En attente de l\'adresse du tunnel pour la publier…';
    return `
      <span class="section-label mt-sm" style="display:block">Lien fixe à partager</span>
      <div class="sys-url-row">
        <code class="sys-url-value">${esc(portal.portal_url)}</code>
        <button type="button" class="btn btn-sm" id="btn-copy-portal" data-url="${esc(portal.portal_url)}">Copier</button>
        ${portal.published_url && !portal.error ? '<span class="chip chip-gold">lien fixe</span>' : ''}
      </div>
      <div class="muted small">${status} Il ne change jamais : c'est celui à donner aux joueurs.</div>`;
  }

  function jokersOfTeamHtml(teamId) {
    const list = (state.jokers || []).filter((j) => j.team_id === teamId);
    if (!list.length) return '';
    return `<div class="ta-jokers">${list.map((j) => `<div class="flex wrap" style="gap:8px;align-items:center">
      <span class="chip chip-gold">🃏 Joker</span>
      <span class="muted small">${esc(j.day_label || j.day)} à ${esc(App.formatTime(j.activated_at))}${j.player_name ? ` par ${esc(j.player_name)}` : ''} · +${j.extra_games} parties</span>
      <button type="button" class="btn btn-sm btn-ghost" data-cancel-joker="${j.id}">Annuler</button>
    </div>`).join('')}</div>`;
  }

  async function cancelJoker(id, btn) {
    const res = await App.confirm({ title: 'Annuler ce joker ?', message: 'Le duo récupère son joker ; les parties en plus de ce jour-là ne comptent plus.', confirmText: 'Annuler le joker', danger: true });
    if (!res.ok) return;
    App.setLoading(btn, true);
    try {
      const r = await admin(() => api(`/api/admin/jokers/${id}`, { method: 'DELETE', admin: true }));
      if (r !== undefined) toast('Joker annulé.', { type: 'success' });
      await load();
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(btn, false);
    }
  }

  function renderPublicUrl() {
    if (!els.sysUrl) return;
    const pub = (state && state.public_url) || null;
    const url = (pub && pub.url) || (state && state.base_url) || '';
    const source = pub ? pub.source : 'local';
    let help;
    const fixed = !!(pub && pub.fixed);
    if (source === 'tunnel' && fixed) {
      help = 'Lien fixe Tailscale : c\'est celui à partager aux joueurs, il ne change pas d\'un lancement à l\'autre. Les messages Discord l\'utilisent.';
    } else if (source === 'tunnel') {
      help = `Adresse du tunnel Cloudflare détectée automatiquement${pub.detected_at ? ` (${esc(App.timeAgo(pub.detected_at))})` : ''} : c'est celle à partager aux joueurs. Elle change à chaque relance du tunnel : pour un lien fixe, mets TUNNEL=tailscale dans .env (voir README, « Lien fixe »). Les messages Discord l'utilisent.`;
    } else if (source === 'env') {
      help = 'Lien fixe défini par BASE_URL dans .env : c\'est celui à partager aux joueurs (utilisé dans les messages Discord).';
    } else {
      help = 'Adresse locale : seul ce PC y accède. Lance Tunnel.bat (ou laisse PekinExpress.bat le faire) pour obtenir une adresse à partager ; elle s\'affichera ici automatiquement.';
    }
    els.sysUrl.innerHTML = `
      <span class="section-label">Adresse du site</span>
      <div class="sys-url-row">
        <code class="sys-url-value">${esc(url || '—')}</code>
        <button type="button" class="btn btn-sm" id="btn-copy-url">Copier</button>
        ${source === 'tunnel' ? '<span class="chip chip-success">tunnel actif</span>' : ''}${fixed ? '<span class="chip chip-gold">lien fixe</span>' : (source === 'tunnel' ? '<span class="chip">change à chaque lancement</span>' : '')}
      </div>
      <div class="muted small">${help}</div>${portalHtml(state && state.portal)}`;
    const portalBtn = $('#btn-copy-portal');
    if (portalBtn) portalBtn.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(portalBtn.dataset.url); toast('Lien fixe copié.', { type: 'success' }); }
      catch (e) { toast('Copie impossible : sélectionne le lien à la main.', { type: 'error' }); }
    });
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
    els.liveCheck.innerHTML = `<div class="muted" style="font-size:.85em">Vérifié à ${esc(when)} · ${esc(every)}${r && r.demo_mode ? ' · mode démo (parties simulées)' : ''}${r && r.key_hint ? ` · clé Riot se terminant par <code>${esc(r.key_hint)}</code>` : ''}</div><ul style="list-style:none;margin:6px 0 0;padding:0">${rows}</ul>${empty}`;
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

  /* Diagnostic du test Discord : message envoyé ? mention du rôle retenue par Discord ? GIF ? */
  function discordReportHtml(r) {
    const line = (ok, html) => `<li class="diag-line ${ok === true ? 'is-ok' : ok === false ? 'is-ko' : 'is-warn'}"><span class="diag-icon">${ok === true ? '✅' : ok === false ? '❌' : '⚠️'}</span><span>${html}</span></li>`;
    const items = [];
    items.push(r.sent ? line(true, 'Message de test envoyé sur Discord.') : line(false, `Rien envoyé : ${esc(r.error || "échec de l'envoi")}.`));
    if (r.role_id_invalid) {
      items.push(line(false, `<strong>DISCORD_ROLE_ID</strong> ne contient pas un identifiant de rôle (« ${esc(r.role_id_invalid)} »). Il faut le numéro du rôle seul (17 à 20 chiffres) : Discord → Paramètres du serveur → Rôles → « ⋯ » à côté de PekinExpress → « Copier l'identifiant du rôle ».`));
    } else if (!r.role_mention) {
      items.push(line(null, 'Pas de mention : <strong>DISCORD_ROLE_ID</strong> est vide dans .env.'));
    } else if (r.role_recognized === false) {
      items.push(line(false, `Discord n'a <strong>pas</strong> retenu la mention du rôle (ID ${esc(r.role_id)}). Soit le rôle n'est pas mentionnable : Paramètres du serveur → Rôles → PekinExpress → « Permettre à tout le monde de @mentionner ce rôle » → Enregistrer. Soit l'identifiant n'est pas celui d'un rôle de ce serveur (le message affiche alors « @rôle-inconnu »).`));
    } else if (r.role_recognized === true) {
      items.push(line(true, `Discord a bien reçu la mention du rôle (ID ${esc(r.role_id)}).`));
    } else if (r.sent) {
      items.push(line(null, `Mention du rôle envoyée (ID ${esc(r.role_id)}), sans confirmation de Discord.`));
    }
    items.push(r.klipy ? line(true, 'GIF Klipy activés.') : line(null, 'Pas de GIF Klipy : <strong>KLIPY_API_KEY</strong> est vide dans .env.'));
    const checklist = r.sent && r.role_mention && !r.role_id_invalid ? `
      <p class="muted" style="margin:14px 0 6px">Pas de son malgré la mention ? Vérifie dans Discord :</p>
      <ol class="diag-check">
        <li>Le rôle est <strong>mentionnable</strong> : Paramètres du serveur → Rôles → PekinExpress → « Permettre à tout le monde de @mentionner ce rôle ».</li>
        <li><strong>Tu as le rôle</strong> toi-même (clic sur ton pseudo dans la liste des membres) : seuls ses membres sont notifiés.</li>
        <li>Tu ne regardais pas déjà ce salon : Discord ne sonne pas pour le salon ouvert à l'écran. Refais le test depuis un autre salon, ou Discord réduit.</li>
        <li>Clic droit sur le serveur → Paramètres de notification : « Supprimer toutes les mentions de rôle » est <strong>décoché</strong>, et le serveur ou le salon n'est pas en sourdine.</li>
        <li>Ton statut n'est pas « Ne pas déranger ». Sur téléphone : pas de notification tant que Discord est actif sur le PC.</li>
      </ol>` : '';
    return `<ul class="diag-list">${items.join('')}</ul>${checklist}`;
  }

  /* Annonce « c'est parti » : état (envoyée, en attente, abandonnée…) puis envoi à la main. */
  els.btnAnnounce.addEventListener('click', async () => {
    App.setLoading(els.btnAnnounce, true);
    try {
      const st = await admin(() => api('/api/admin/announce-start', { admin: true }));
      if (st === undefined) return;
      const info = `<p><strong>État :</strong> ${esc(st.message || '')}</p>
        <p class="muted" style="font-size:13px">Challenge : ${esc(st.status || '?')} · début enregistré : ${esc(st.start_at || '—')}${st.announced_at ? ` · dernière annonce : ${esc(st.announced_at)}` : ''}</p>
        ${st.status !== 'running' ? '<p>⚠️ Démarre d’abord le challenge (bouton « 🚀 Démarrer » en haut de l’Admin), sinon les parties ne sont pas suivies.</p>' : ''}`;
      App.openModal({
        title: 'Annonce du début sur Discord',
        html: `${info}<p>Envoyer l’annonce « 🚀 C’est parti » maintenant (mention du rôle, duos, GIF) ?</p>`,
        actions: [
          { label: 'Annuler', className: 'btn-ghost', onClick: ({ close }) => close() },
          {
            label: '📣 Envoyer maintenant',
            className: 'btn-primary',
            onClick: async ({ close, button }) => {
              App.setLoading(button, true);
              try {
                const r = await admin(() => api('/api/admin/announce-start', { method: 'POST', admin: true, body: {} }));
                close();
                if (r && r.sent) toast('📣 Annonce du début envoyée sur Discord.', { type: 'success' });
                else if (r) toast(`Annonce non envoyée : ${r.error || 'Discord a refusé le message'}.`, { type: 'error', timeout: 10000 });
              } catch (err) {
                toast(err.message, { type: 'error' });
              } finally {
                App.setLoading(button, false);
              }
            },
          },
        ],
      });
    } catch (err) {
      toast(err.message, { type: 'error' });
    } finally {
      App.setLoading(els.btnAnnounce, false);
    }
  });

  els.btnDiscord.addEventListener('click', async () => {
    App.setLoading(els.btnDiscord, true);
    try {
      // Relit .env d'abord : le test utilise toujours les valeurs que tu viens d'enregistrer
      await admin(() => api('/api/admin/reload-settings', { method: 'POST', admin: true, body: {} }));
      const r = await admin(() => api('/api/admin/test-notification', { method: 'POST', admin: true, body: {} }));
      if (r !== undefined && r) {
        App.openModal({
          title: 'Test Discord',
          html: discordReportHtml(r),
          actions: [{ label: 'Fermer', className: 'btn-primary', onClick: ({ close }) => close() }],
        });
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
    joker_used: () => { if (!els.panel.hidden) load(); },
    joker_cancelled: () => { if (!els.panel.hidden) load(); },
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
