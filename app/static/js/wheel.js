/* La roue : tirage des duos animé sur canvas (requestAnimationFrame). */
(function () {
  'use strict';
  const App = window.App;
  const { $, api, toast, escapeHtml: esc, avatar } = App;

  /* Palette douce (texte sombre lisible dessus). Une couleur par joueur, fixée à l'index d'origine. */
  const SEGMENT_COLORS = ['#e8c56a', '#7fb0f2', '#79cf9e', '#f28c8c', '#c59af2', '#7fd1c8', '#f2b07a', '#f29ac4', '#b9c6d6', '#a8d86f'];
  const TAU = Math.PI * 2;
  const POINTER = -Math.PI / 2; // la flèche est en haut

  const canvas = $('#wheel');
  const ctx = canvas.getContext('2d');
  const els = {
    status: $('#wheel-status'),
    actions: $('#wheel-actions'),
    help: $('#wheel-help'),
    duos: $('#wheel-duos'),
    duosCount: $('#duos-count'),
    blocked: $('#wheel-blocked'),
    layout: $('#wheel-layout'),
    headStatus: $('#wheel-head-status'),
    sub: $('#wheel-sub'),
  };

  let state = null;          // /api/state
  let allPlayers = [];       // joueurs liés et actifs (ordre stable)
  let remaining = [];        // joueurs encore sur la roue
  let angle = 0;             // rotation courante (radians)
  let highlight = -1;        // index (dans remaining) du segment mis en avant
  let spinning = false;
  let dpr = 1;
  let size = 560;
  let rafId = null;

  /* ------------------------------------------------------------------ */
  /* Dessin                                                               */
  /* ------------------------------------------------------------------ */
  function resize() {
    const rect = canvas.getBoundingClientRect();
    const css = Math.max(240, Math.round(rect.width || 560));
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== css * dpr) {
      canvas.width = css * dpr;
      canvas.height = css * dpr;
    }
    size = css;
    draw();
  }

  function colorOf(player) {
    const idx = allPlayers.findIndex((p) => p.id === player.id);
    return SEGMENT_COLORS[(idx >= 0 ? idx : 0) % SEGMENT_COLORS.length];
  }

  function draw() {
    const n = remaining.length;
    const R = size / 2;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.save();
    ctx.translate(R, R);

    // Disque de fond
    ctx.beginPath();
    ctx.arc(0, 0, R - 2, 0, TAU);
    ctx.fillStyle = '#171d2a';
    ctx.fill();

    if (n === 0) {
      ctx.fillStyle = '#5f6778';
      ctx.font = `600 ${Math.round(size * 0.04)}px Inter, system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(spinning || allPlayers.length ? 'Tous les joueurs sont tirés' : 'Aucun joueur', 0, 0);
      ctx.restore();
      return;
    }

    ctx.rotate(angle);
    const step = TAU / n;
    const fontSize = Math.max(12, Math.min(22, Math.round(size * (n <= 4 ? 0.045 : n <= 8 ? 0.038 : 0.03))));
    const textR = R - Math.round(size * 0.05);
    const maxTextW = R * (n === 1 ? 1.2 : 0.62);

    for (let i = 0; i < n; i++) {
      const start = i * step;
      const end = start + step;
      const p = remaining[i];
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, R - 4, start, end);
      ctx.closePath();
      ctx.fillStyle = colorOf(p);
      ctx.fill();
      if (n > 1) {
        ctx.strokeStyle = '#0b0e14';
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      if (i === highlight) {
        ctx.fillStyle = 'rgba(255,255,255,0.28)';
        ctx.fill();
        ctx.strokeStyle = '#e5b64d';
        ctx.lineWidth = 5;
        ctx.stroke();
      }
      // Pseudo le long du rayon
      ctx.save();
      ctx.rotate(start + step / 2);
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#0b0e14';
      ctx.font = `700 ${fontSize}px Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`;
      let label = String(p.display_name || '?');
      while (label.length > 2 && ctx.measureText(label).width > maxTextW) label = label.slice(0, -1);
      if (label !== p.display_name) label = label.replace(/\s+$/, '') + '…';
      ctx.fillText(label, n === 1 ? ctx.measureText(label).width / 2 : textR, 0);
      ctx.restore();
    }

    // Moyeu
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(14, size * 0.07), 0, TAU);
    ctx.fillStyle = '#121722';
    ctx.fill();
    ctx.strokeStyle = '#e5b64d';
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(4, size * 0.016), 0, TAU);
    ctx.fillStyle = '#e5b64d';
    ctx.fill();
    ctx.restore();
  }

  /* ------------------------------------------------------------------ */
  /* Animation                                                            */
  /* ------------------------------------------------------------------ */
  const sleep = (ms) => new Promise((r) => setTimeout(r, App.reducedMotion ? Math.min(ms, 120) : ms));
  const easeOutQuart = (t) => 1 - Math.pow(1 - t, 4);
  const mod = (a, m) => ((a % m) + m) % m;

  /* Fait tourner la roue pour que la flèche s'arrête sur `remaining[index]`. */
  function spinTo(index) {
    return new Promise((resolve) => {
      const n = remaining.length;
      const step = TAU / n;
      const center = index * step + step / 2;
      // Petit décalage pour ne pas tomber toujours pile au milieu du segment
      const jitter = (Math.random() - 0.5) * step * 0.55;
      const desired = mod(POINTER - center + jitter, TAU);
      const current = mod(angle, TAU);
      let delta = mod(desired - current, TAU);
      if (delta < 0.2) delta += TAU;
      const turns = 3 + Math.floor(Math.random() * 2); // 3 à 4 tours complets
      const total = turns * TAU + delta;
      const from = angle;
      const to = angle + total;
      const duration = App.reducedMotion ? 0 : 2300 + Math.random() * 400;

      if (duration === 0) { angle = to; draw(); resolve(); return; }
      const t0 = performance.now();
      const frame = (now) => {
        const t = Math.min(1, (now - t0) / duration);
        angle = from + total * easeOutQuart(t);
        draw();
        if (t < 1) rafId = requestAnimationFrame(frame);
        else { angle = to; draw(); resolve(); }
      };
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(frame);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Panneau des duos                                                     */
  /* ------------------------------------------------------------------ */
  function playerById(id) {
    return (state.players || []).find((p) => p.id === id) || allPlayers.find((p) => p.id === id);
  }

  function duoCardHtml(team, players, opts) {
    opts = opts || {};
    const color = team.color || '#e5b64d';
    const rows = [0, 1].map((i) => {
      const p = players[i];
      if (!p) return `<div class="duo-player pending">${avatar({ name: '?', color: '#5f6778' })}<div class="grow"><div class="name">En attente…</div></div></div>`;
      return `<div class="duo-player">${avatar({ name: p.display_name, src: p.icon_url, color })}
        <div class="grow truncate"><div class="name">${esc(p.display_name)}</div>
        <div class="sub rank" style="--rank-color:${esc(p.rank_color || App.rankColor(p.tier))}">${esc(p.rank_label || App.formatRank(p.tier, p.rank, p.lp))}</div></div></div>`;
    }).join('');
    return `<div class="card duo-card ${opts.pop ? 'pop' : ''}" style="--team-color:${esc(color)}" data-slot="${team.slot || ''}">
      <div class="duo-name"><span class="swatch"></span>${esc(team.name || 'Duo')}</div>
      <div class="duo-players">${rows}</div>
    </div>`;
  }

  function renderDuos(teams, revealed) {
    // revealed : nombre de joueurs déjà tirés (ordre), ou null = tout afficher
    const sorted = (teams || []).slice().sort((a, b) => (a.slot || 0) - (b.slot || 0));
    els.duosCount.textContent = sorted.length ? `${sorted.length} duos` : '';
    if (!sorted.length) {
      const n = Math.floor(allPlayers.length / 2);
      els.duos.innerHTML = Array.from({ length: n }, (_, i) => `<div class="duo-placeholder">Duo ${i + 1}</div>`).join('');
      return;
    }
    els.duos.innerHTML = sorted.map((t) => duoCardHtml(t, (t.player_ids || []).map(playerById).filter(Boolean))).join('');
  }

  /* ------------------------------------------------------------------ */
  /* Déroulé d'un tirage                                                  */
  /* ------------------------------------------------------------------ */
  async function animateDraw(result) {
    const order = result.order || [];
    const teams = (result.teams || []).slice();
    if (!order.length) throw new Error('Le tirage est vide.');
    spinning = true;
    App.mutedEvents.add('draw_done');
    els.actions.innerHTML = '';
    els.help.textContent = '';
    remaining = allPlayers.slice();
    highlight = -1;
    draw();

    const pairs = Math.ceil(order.length / 2);
    const picked = [];
    const cards = [];
    els.duos.innerHTML = Array.from({ length: pairs }, (_, i) => `<div class="duo-placeholder" data-pair="${i}">Duo ${i + 1}</div>`).join('');
    els.duosCount.textContent = `${pairs} duos`;

    for (let k = 0; k < order.length; k++) {
      const idx = remaining.findIndex((p) => p.id === order[k]);
      if (idx === -1) continue; // joueur inconnu localement (état périmé) : on l'ignore
      els.status.innerHTML = `<span class="muted">Tirage ${k + 1} / ${order.length}…</span>`;
      await spinTo(idx);
      const p = remaining[idx];
      highlight = idx;
      draw();
      picked.push(p);
      els.status.innerHTML = `<span class="picked">${avatar({ name: p.display_name, src: p.icon_url, color: colorOf(p), size: 'sm' })}<strong>${esc(p.display_name)}</strong></span>`;

      const pairIdx = Math.floor(k / 2);
      const a = order[pairIdx * 2];
      const b = order[pairIdx * 2 + 1];
      const team = teams.find((t) => (t.player_ids || []).includes(a) && (t.player_ids || []).includes(b)) || teams[pairIdx] || { name: `Duo ${pairIdx + 1}`, color: '#e5b64d', slot: pairIdx + 1 };
      const members = [playerById(a), k % 2 === 1 ? playerById(b) : null];
      const slot = els.duos.querySelector(`[data-pair="${pairIdx}"]`) || cards[pairIdx];
      const wrapper = document.createElement('div');
      wrapper.innerHTML = duoCardHtml(team, members, { pop: true });
      const card = wrapper.firstElementChild;
      card.dataset.pair = String(pairIdx);
      if (slot) slot.replaceWith(card);
      cards[pairIdx] = card;

      await sleep(800);
      highlight = -1;
      remaining.splice(idx, 1);
      draw();
      await sleep(350);
    }

    spinning = false;
    App.mutedEvents.delete('draw_done');
    els.status.innerHTML = '<strong>Les duos sont tirés ! 🎉</strong>';
    toast('🎡 Les duos ont été tirés !', { type: 'success' });
    try { state = await api('/api/state'); } catch (e) { /* on garde l'état local */ }
    renderDrawnActions();
  }

  /* ------------------------------------------------------------------ */
  /* Actions                                                              */
  /* ------------------------------------------------------------------ */
  function button(label, cls, onClick, id) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `btn ${cls || ''}`;
    b.textContent = label;
    if (id) b.id = id;
    b.addEventListener('click', () => onClick(b));
    return b;
  }

  /* Recharge l'état et reconstruit la liste des joueurs sur la roue à partir de `order`. */
  async function prepareForDraw(order) {
    try { state = await api('/api/state'); } catch (e) { /* on garde l'état connu */ }
    const ids = new Set(order || []);
    const players = (state.players || []).filter((p) => ids.has(p.id));
    if (players.length) allPlayers = players.sort((a, b) => (a.id || 0) - (b.id || 0));
    els.blocked.hidden = true;
    els.layout.hidden = false;
    resize();
  }

  function resetSpin() {
    spinning = false;
    App.mutedEvents.delete('draw_done');
  }

  async function launchDraw(btn) {
    if (spinning) return;
    App.setLoading(btn, true);
    // Le serveur publie `draw_done` (SSE) avant même de répondre au POST :
    // on verrouille tout de suite pour ne pas lancer deux animations.
    spinning = true;
    App.mutedEvents.add('draw_done');
    let result;
    try {
      result = await App.adminAction(() => api('/api/admin/draw', { method: 'POST', admin: true, body: {} }));
    } catch (e) {
      resetSpin();
      App.setLoading(btn, false);
      toast(e.message, { type: 'error' });
      return;
    }
    if (result === undefined) { resetSpin(); App.setLoading(btn, false); return; }
    try {
      await prepareForDraw(result.order);
      await animateDraw(result);
    } catch (e) {
      resetSpin();
      App.setLoading(btn, false);
      toast(e.message, { type: 'error' });
      load();
    }
  }

  async function startChallenge(btn) {
    const res = await App.confirm({ title: 'Démarrer le challenge ?', message: 'Les parties jouées à partir de maintenant compteront pour le classement. Les joueurs seront prévenus.', confirmText: '🚀 Démarrer' });
    if (!res.ok) return;
    App.setLoading(btn, true);
    try {
      const r = await App.adminAction(() => api('/api/admin/challenge/start', { method: 'POST', admin: true, body: {} }));
      if (r === undefined) { App.setLoading(btn, false); return; }
      window.location.href = '/dashboard';
    } catch (e) {
      App.setLoading(btn, false);
      toast(e.message, { type: 'error' });
    }
  }

  function renderReadyActions() {
    els.actions.innerHTML = '';
    els.actions.appendChild(button('🎡 Lancer la roue', 'btn-primary btn-lg', launchDraw, 'btn-spin'));
    els.help.textContent = "Réservé à l'organisateur (mot de passe demandé).";
  }

  function renderDrawnActions() {
    els.actions.innerHTML = '';
    els.actions.appendChild(button('Relancer la roue', 'btn-ghost', launchDraw, 'btn-respin'));
    els.actions.appendChild(button('🚀 Démarrer le challenge', 'btn-primary btn-lg', startChallenge, 'btn-start'));
    els.help.textContent = "Le challenge commence quand l'organisateur clique sur Démarrer.";
    els.headStatus.innerHTML = App.statusChip('drawn');
  }

  function renderRunningActions(status) {
    els.actions.innerHTML = `<a class="btn btn-primary btn-lg" href="/dashboard">Voir le classement</a>`;
    els.help.textContent = status === 'running' ? 'Le challenge est en cours : les duos sont figés.' : 'Le challenge est terminé.';
    els.status.innerHTML = '';
    els.headStatus.innerHTML = App.statusChip(status);
  }

  /* ------------------------------------------------------------------ */
  /* Chargement                                                           */
  /* ------------------------------------------------------------------ */
  function blocked(message, linkHtml) {
    els.blocked.hidden = false;
    els.layout.hidden = true;
    els.blocked.innerHTML = `<div class="empty-icon">🎡</div><div class="empty-title">${esc(message)}</div>${linkHtml || '<a class="btn btn-primary" href="/">Retour à l\'accueil</a>'}`;
  }

  async function load() {
    try {
      state = await api('/api/state');
    } catch (e) {
      blocked('Impossible de charger les joueurs', `<p>${esc(e.message)}</p><button type="button" class="btn btn-primary" onclick="location.reload()">Réessayer</button>`);
      return;
    }
    const status = (state.challenge && state.challenge.status) || 'registration';
    const players = (state.players || []).filter((p) => p.active !== false && p.is_linked);
    allPlayers = players.slice().sort((a, b) => (a.id || 0) - (b.id || 0));
    remaining = allPlayers.slice();
    highlight = -1;
    els.headStatus.innerHTML = App.statusChip(status);

    if (status === 'registration') {
      if (allPlayers.length < 2) {
        blocked('Il faut au moins 2 joueurs avec un compte lié pour lancer la roue.', '<a class="btn btn-primary" href="/">Inscrire les joueurs</a>');
        return;
      }
      if (allPlayers.length % 2 !== 0) {
        blocked(`${allPlayers.length} joueurs liés : il faut un nombre pair pour former des duos.`, '<a class="btn btn-primary" href="/">Ajuster les joueurs</a>');
        return;
      }
      els.blocked.hidden = true;
      els.layout.hidden = false;
      resize();
      renderDuos([]);
      els.status.innerHTML = `<span class="muted">${allPlayers.length} joueurs prêts · ${allPlayers.length / 2} duos à tirer</span>`;
      renderReadyActions();
      return;
    }

    // Duos déjà tirés : on affiche l'existant
    els.blocked.hidden = true;
    els.layout.hidden = false;
    // Sur la roue : tous les joueurs des duos (même ceux désactivés depuis)
    const teamPlayerIds = new Set((state.teams || []).flatMap((t) => t.player_ids || []));
    const drawnPlayers = (state.players || []).filter((p) => teamPlayerIds.has(p.id));
    if (drawnPlayers.length) {
      allPlayers = drawnPlayers.slice().sort((a, b) => (a.id || 0) - (b.id || 0));
      remaining = allPlayers.slice();
    }
    resize();
    renderDuos(state.teams, null);
    if (status === 'drawn') {
      els.status.innerHTML = '<span class="muted">Les duos ont déjà été tirés. Tu peux relancer la roue avant de démarrer.</span>';
      renderDrawnActions();
    } else {
      renderRunningActions(status);
    }
  }

  /* ------------------------------------------------------------------ */
  window.addEventListener('resize', App.debounce(resize, 120));
  if ('ResizeObserver' in window) new ResizeObserver(App.debounce(resize, 60)).observe(canvas.parentElement);

  App.connectEvents({
    draw_done: (d) => {
      if (spinning) return; // c'est notre propre tirage, déjà en cours d'animation
      if (d && Array.isArray(d.order) && d.order.length && Array.isArray(d.teams)) {
        // Un autre visiteur a lancé la roue : on rejoue l'animation ici aussi
        spinning = true;
        App.mutedEvents.add('draw_done');
        prepareForDraw(d.order).then(() => animateDraw(d)).catch(() => { resetSpin(); load(); });
      } else {
        load();
      }
    },
    challenge_started: () => load(),
    challenge_reset: () => load(),
    challenge_finished: () => load(),
    player_linked: () => { if (!spinning && state && state.challenge && state.challenge.status === 'registration') load(); },
    player_registered: () => { if (!spinning && state && state.challenge && state.challenge.status === 'registration') load(); },
  });

  load();
})();
