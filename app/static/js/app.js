/* Pékin Express LoL — helpers partagés par toutes les pages.
   Expose `window.App` : api(), toast(), connectEvents(), requireAdmin(), helpers de formatage. */
(function () {
  'use strict';

  const App = (window.App = window.App || {});
  const body = document.body;
  const ADMIN_KEY = 'pekin_admin_password';

  App.page = body.dataset.page || '';
  App.demoMode = body.dataset.demo === 'true';
  App.assetVersion = body.dataset.asset || '';
  App.gamesPerDay = parseInt(body.dataset.gamesPerDay || '10', 10) || 10;
  App.reducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  /* ------------------------------------------------------------------ */
  /* Petits utilitaires DOM                                              */
  /* ------------------------------------------------------------------ */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  App.$ = $;
  App.$$ = $$;

  App.debounce = function (fn, ms) {
    let t = null;
    return function () {
      const args = arguments;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, args), ms);
    };
  };

  App.escapeHtml = function (value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };
  const esc = App.escapeHtml;

  App.setLoading = function (btn, loading) {
    if (!btn) return;
    btn.classList.toggle('is-loading', !!loading);
    btn.disabled = !!loading;
  };

  /* ------------------------------------------------------------------ */
  /* Formatage                                                            */
  /* ------------------------------------------------------------------ */
  App.timeAgoSeconds = function (s) {
    if (s === null || s === undefined || isNaN(s)) return '—';
    s = Math.max(0, Math.round(s));
    if (s < 45) return "à l'instant";
    const m = Math.round(s / 60);
    if (m < 60) return `il y a ${m} min`;
    const h = Math.floor(s / 3600);
    if (h < 24) return `il y a ${h} h`;
    const d = Math.floor(s / 86400);
    return `il y a ${d} j`;
  };

  App.timeAgo = function (iso) {
    if (!iso) return '—';
    const t = typeof iso === 'number' ? iso : Date.parse(iso);
    if (isNaN(t)) return '—';
    return App.timeAgoSeconds((Date.now() - t) / 1000);
  };

  App.formatDuration = function (seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return '—';
    seconds = Math.max(0, Math.floor(seconds));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const mm = h ? String(m).padStart(2, '0') : String(m);
    return `${h ? h + ':' : ''}${mm}:${String(s).padStart(2, '0')}`;
  };

  App.formatLp = function (n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    n = Math.round(n);
    if (n > 0) return `+${n} LP`;
    if (n < 0) return `−${Math.abs(n)} LP`;
    return '0 LP';
  };

  App.lpClass = function (n) {
    if (n === null || n === undefined || isNaN(n)) return 'lp-zero';
    return n > 0 ? 'lp-pos' : n < 0 ? 'lp-neg' : 'lp-zero';
  };

  App.lpHtml = function (n, extraClass) {
    return `<span class="lp ${App.lpClass(n)} ${extraClass || ''}">${esc(App.formatLp(n))}</span>`;
  };

  App.formatPct = function (v) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return `${Math.round(v)} %`;
  };

  App.formatNumber = function (v, digits) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toLocaleString('fr-FR', { maximumFractionDigits: digits === undefined ? 0 : digits });
  };

  App.formatDateTime = function (iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  };

  App.formatTime = function (iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  };

  App.statusLabel = function (status) {
    switch (status) {
      case 'registration': return 'Inscriptions ouvertes';
      case 'drawn': return 'Duos formés';
      case 'running': return 'Challenge en cours';
      case 'finished': return 'Terminé';
      default: return status || '—';
    }
  };

  App.statusChip = function (status) {
    const cls = { registration: 'chip-blue', drawn: 'chip-gold', running: 'chip-green', finished: 'chip' }[status] || 'chip';
    const dot = status === 'running' ? '<span class="dot" style="width:7px;height:7px;border-radius:50%;background:currentColor"></span>' : '';
    return `<span class="chip ${cls} chip-lg">${dot}${esc(App.statusLabel(status))}</span>`;
  };

  App.queueLabel = function (queueId, gameMode) {
    if (queueId === 420 || queueId === '420' || queueId === 'SOLO') return 'Solo/Duo';
    if (queueId === 440 || queueId === '440' || queueId === 'FLEX') return 'Flex';
    return gameMode || 'Partie';
  };

  const TIERS = ['IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD', 'DIAMOND', 'MASTER', 'GRANDMASTER', 'CHALLENGER'];
  const DIVISIONS = ['IV', 'III', 'II', 'I'];
  const RANK_COLORS = {
    IRON: '#8a8a8a', BRONZE: '#b07a4a', SILVER: '#a9b4c0', GOLD: '#e5b64d', PLATINUM: '#4fb8a8',
    EMERALD: '#3fbf7f', DIAMOND: '#5aa0ff', MASTER: '#b465f0', GRANDMASTER: '#f05a5a', CHALLENGER: '#7fe0ff', UNRANKED: '#6b7280',
  };
  App.RANK_COLORS = RANK_COLORS;

  App.tierName = function (tier) {
    if (!tier) return 'Unranked';
    const t = String(tier).toLowerCase();
    return t.charAt(0).toUpperCase() + t.slice(1);
  };

  App.rankColor = function (tier) {
    return RANK_COLORS[(tier || 'UNRANKED').toUpperCase()] || RANK_COLORS.UNRANKED;
  };

  App.formatRank = function (tier, rank, lp) {
    if (!tier) return 'Unranked';
    const name = App.tierName(tier);
    return rank ? `${name} ${rank} · ${lp || 0} LP` : `${name} · ${lp || 0} LP`;
  };

  /* LP absolus → libellé court ("Gold IV", "Master +120"), pour les axes du graphe. */
  App.rankFromAbsolute = function (v, short) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    v = Math.round(v);
    if (v >= 2800) {
      const lp = v - 2800;
      return short ? 'Master+' : `Master +${lp}`;
    }
    const tierIdx = Math.min(TIERS.length - 1, Math.max(0, Math.floor(v / 400)));
    const rest = v - tierIdx * 400;
    const div = Math.min(3, Math.floor(rest / 100));
    const lp = rest - div * 100;
    const base = `${App.tierName(TIERS[tierIdx])} ${DIVISIONS[div]}`;
    return lp && !short ? `${base} +${lp}` : base;
  };

  App.initials = function (name) {
    const parts = String(name || '?').trim().split(/[\s_\-.]+/).filter(Boolean);
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  };

  /* Avatar : <img> DDragon qui, en cas d'échec, laisse apparaître les initiales. */
  App.avatar = function (opts) {
    opts = opts || {};
    const size = opts.size || '';
    const color = opts.color || RANK_COLORS.UNRANKED;
    const cls = ['avatar', size ? `avatar-${size}` : '', opts.square ? 'avatar-square' : '', opts.className || ''].filter(Boolean).join(' ');
    const img = opts.src
      ? `<img src="${esc(opts.src)}" alt="" loading="lazy" onerror="this.parentNode.classList.add('is-broken')">`
      : '';
    return `<span class="${cls}" style="--avatar-color:${esc(color)}" title="${esc(opts.title || opts.name || '')}"><span class="avatar-initials" aria-hidden="true">${esc(App.initials(opts.name))}</span>${img}</span>`;
  };

  /* ------------------------------------------------------------------ */
  /* Images du jeu (Data Dragon / Community Dragon) — repli obligatoire   */
  /* ------------------------------------------------------------------ */
  const IMG_FALLBACK = "this.parentNode.classList.add('is-broken')";

  App.positionLabel = function (pos, short) {
    const full = { TOP: 'Top', JUNGLE: 'Jungle', MIDDLE: 'Mid', BOTTOM: 'ADC', UTILITY: 'Support' };
    const abbr = { TOP: 'Top', JUNGLE: 'Jgl', MIDDLE: 'Mid', BOTTOM: 'Bot', UTILITY: 'Sup' };
    const key = String(pos || '').toUpperCase();
    return (short ? abbr : full)[key] || (pos ? String(pos) : '');
  };

  /* Winrate → classe de couleur (≥ 55 % vert, ≤ 45 % rouge). */
  App.wrClass = function (wr) {
    if (wr === null || wr === undefined || isNaN(wr)) return 'muted';
    return wr >= 55 ? 'wr-good' : wr <= 45 ? 'wr-bad' : 'wr-mid';
  };

  /* Icône de champion carrée : initiales en repli, badge de niveau optionnel, `fav` = mise en avant. */
  App.champIcon = function (opts) {
    opts = opts || {};
    const name = opts.name || '?';
    const cls = ['champ-icon', opts.size ? `champ-icon-${opts.size}` : '', opts.fav ? 'is-fav' : '', opts.className || '', opts.src ? '' : 'is-broken'].filter(Boolean).join(' ');
    const img = opts.src ? `<img src="${esc(opts.src)}" alt="" ${opts.eager ? '' : 'loading="lazy"'} onerror="${IMG_FALLBACK}">` : '';
    const level = opts.level ? `<span class="champ-level">${esc(opts.level)}</span>` : '';
    return `<span class="${cls}" title="${esc(opts.title || name)}"><span class="champ-initials" aria-hidden="true">${esc(App.initials(name))}</span>${img}${level}</span>`;
  };

  /* Emblème (PNG) ou mini-écusson (SVG) de rang : rien sans URL, disparaît si le chargement échoue. */
  App.rankEmblem = function (src, size, title) {
    if (!src) return '';
    return `<img class="rank-emblem${size ? ` rank-emblem-${size}` : ''}" src="${esc(src)}" alt="" title="${esc(title || '')}" loading="lazy" onerror="this.remove()">`;
  };

  /* Rangée des 7 objets (6 + bibelot) ; emplacement vide ou image cassée = case sombre. */
  App.itemRow = function (urls, opts) {
    opts = opts || {};
    const list = (Array.isArray(urls) ? urls : []).slice(0, 7);
    while (list.length < 7) list.push(null);
    const slots = list.map((u, i) => `<span class="item-slot${i === 6 ? ' is-trinket' : ''}${u ? '' : ' is-empty'}">${u ? `<img src="${esc(u)}" alt="" loading="lazy" onerror="this.parentNode.classList.add('is-empty')">` : ''}</span>`).join('');
    return `<span class="item-row${opts.size ? ` item-row-${opts.size}` : ''}" title="${esc(opts.title || 'Objets')}">${slots}</span>`;
  };

  /* Les deux sorts d'invocateur (empilés). */
  App.spellIcons = function (urls) {
    const list = (Array.isArray(urls) ? urls : []).slice(0, 2);
    while (list.length < 2) list.push(null);
    return `<span class="spell-icons" title="Sorts d'invocateur">${list.map((u) => `<span class="spell-icon${u ? '' : ' is-broken'}">${u ? `<img src="${esc(u)}" alt="" loading="lazy" onerror="${IMG_FALLBACK}">` : ''}</span>`).join('')}</span>`;
  };

  /* Icône de poste ; texte (Top/Jgl/Mid/Bot/Sup) en repli. */
  App.posIcon = function (src, position) {
    const label = App.positionLabel(position, true);
    if (!label) return '';
    const full = App.positionLabel(position);
    if (!src) return `<span class="pos-icon is-broken" data-label="${esc(label)}" title="${esc(full)}"></span>`;
    return `<span class="pos-icon" data-label="${esc(label)}" title="${esc(full)}"><img src="${esc(src)}" alt="" loading="lazy" onerror="${IMG_FALLBACK}"></span>`;
  };

  /* Image de fond (splash) : à placer dans un conteneur `.splash-bg` ; disparaît si elle échoue. */
  App.splashImg = function (src, eager) {
    if (!src) return '';
    return `<img class="splash-bg-img" src="${esc(src)}" alt="" ${eager ? '' : 'loading="lazy"'} onerror="this.remove()">`;
  };

  /* Vignette portrait (loading art) avec double repli : icône carrée puis initiales. */
  App.loadingArt = function (opts) {
    opts = opts || {};
    const name = opts.name || '?';
    const src = opts.src || opts.iconSrc;
    const alt = opts.src && opts.iconSrc ? ` data-alt="${esc(opts.iconSrc)}"` : '';
    const onerr = "if(this.dataset.alt){this.src=this.dataset.alt;this.dataset.alt='';}else{this.parentNode.classList.add('is-broken')}";
    const img = src ? `<img src="${esc(src)}" alt="" loading="lazy"${alt} onerror="${onerr}">` : '';
    return `<span class="loading-art${src ? '' : ' is-broken'}${opts.className ? ` ${opts.className}` : ''}" title="${esc(opts.title || name)}"><span class="champ-initials" aria-hidden="true">${esc(App.initials(name))}</span>${img}</span>`;
  };

  /* ------------------------------------------------------------------ */
  /* API JSON                                                             */
  /* ------------------------------------------------------------------ */
  function detailToMessage(detail, status) {
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const parts = detail.map((d) => (d && d.msg) ? `${(d.loc || []).slice(-1)[0] || ''} : ${d.msg}`.trim() : '').filter(Boolean);
      if (parts.length) return parts.join(' · ');
    }
    if (detail && typeof detail === 'object' && detail.message) return detail.message;
    if (status === 401) return 'Mot de passe administrateur incorrect.';
    if (status === 404) return 'Introuvable.';
    if (status === 400) return 'Requête invalide.';
    if (status >= 500) return 'Erreur côté serveur.';
    return `Erreur ${status}`;
  }

  App.getAdminPassword = function () {
    try { return sessionStorage.getItem(ADMIN_KEY) || ''; } catch (e) { return ''; }
  };
  App.setAdminPassword = function (pw) {
    try { if (pw) sessionStorage.setItem(ADMIN_KEY, pw); else sessionStorage.removeItem(ADMIN_KEY); } catch (e) { /* stockage indisponible */ }
  };

  App.api = async function (path, options) {
    options = options || {};
    const method = (options.method || 'GET').toUpperCase();
    const headers = Object.assign({ Accept: 'application/json' }, options.headers || {});
    const init = { method, headers };
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    if (options.admin) {
      headers['X-Admin-Password'] = typeof options.admin === 'string' ? options.admin : App.getAdminPassword();
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      const err = new Error('Impossible de joindre le serveur.');
      err.status = 0;
      throw err;
    }
    let data = null;
    const text = await res.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
    if (!res.ok) {
      if (res.status === 401 && options.admin) App.setAdminPassword('');
      const err = new Error(detailToMessage(data && data.detail, res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  };

  /* ------------------------------------------------------------------ */
  /* Toasts                                                               */
  /* ------------------------------------------------------------------ */
  App.toast = function (message, opts) {
    opts = opts || {};
    const root = $('#toasts');
    if (!root) return null;
    const type = opts.type || 'info';
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.innerHTML = `<div class="toast-msg">${opts.html ? message : esc(message)}</div><button type="button" class="toast-close" aria-label="Fermer">×</button>`;
    const remove = () => {
      if (el.classList.contains('is-leaving')) return;
      el.classList.add('is-leaving');
      setTimeout(() => el.remove(), 220);
    };
    el.querySelector('.toast-close').addEventListener('click', remove);
    root.appendChild(el);
    while (root.children.length > 5) root.firstChild.remove();
    const timeout = opts.timeout === undefined ? (type === 'error' ? 8000 : 5000) : opts.timeout;
    if (timeout > 0) setTimeout(remove, timeout);
    return el;
  };
  App.toastError = (e) => App.toast((e && e.message) || String(e), { type: 'error' });

  /* ------------------------------------------------------------------ */
  /* Modales                                                              */
  /* ------------------------------------------------------------------ */
  App.openModal = function (opts) {
    opts = opts || {};
    const root = $('#modal-root');
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <h3 id="modal-title">${esc(opts.title || '')}</h3>
      <div class="modal-body">${opts.html || ''}</div>
      <div class="modal-actions"></div>
    </div>`;
    const actions = backdrop.querySelector('.modal-actions');
    const previouslyFocused = document.activeElement;
    const close = () => {
      backdrop.remove();
      document.removeEventListener('keydown', onKey);
      if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
    };
    const onKey = (e) => { if (e.key === 'Escape') { close(); if (opts.onCancel) opts.onCancel(); } };
    document.addEventListener('keydown', onKey);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) { close(); if (opts.onCancel) opts.onCancel(); } });
    (opts.actions || []).forEach((a) => {
      const b = document.createElement('button');
      b.type = a.type || 'button';
      b.className = `btn ${a.className || ''}`;
      b.textContent = a.label;
      b.addEventListener('click', () => a.onClick && a.onClick({ close, modal: backdrop, button: b }));
      actions.appendChild(b);
    });
    root.appendChild(backdrop);
    const first = backdrop.querySelector('input, button.btn-primary, button');
    if (first) setTimeout(() => first.focus(), 20);
    return { el: backdrop, close };
  };

  /* Confirmation : résout `{ok, form}` (form = valeurs des champs nommés du contenu). */
  App.confirm = function (opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      const m = App.openModal({
        title: opts.title || 'Confirmer',
        html: `<p>${opts.html ? opts.message : esc(opts.message || '')}</p>${opts.extraHtml || ''}`,
        onCancel: () => resolve({ ok: false }),
        actions: [
          { label: opts.cancelText || 'Annuler', className: 'btn-ghost', onClick: ({ close }) => { close(); resolve({ ok: false }); } },
          {
            label: opts.confirmText || 'Confirmer',
            className: opts.danger ? 'btn-danger' : 'btn-primary',
            onClick: ({ close, modal }) => {
              const form = {};
              $$('input[name], select[name]', modal).forEach((i) => { form[i.name] = i.type === 'checkbox' ? i.checked : i.value; });
              close();
              resolve({ ok: true, form });
            },
          },
        ],
      });
      return m;
    });
  };

  /* Mot de passe admin : sessionStorage, sinon modale + vérification via POST /api/admin/login. */
  App.requireAdmin = function () {
    const stored = App.getAdminPassword();
    if (stored) return Promise.resolve(stored);
    return new Promise((resolve, reject) => {
      const cancel = () => { const e = new Error('cancelled'); e.cancelled = true; reject(e); };
      const m = App.openModal({
        title: 'Mot de passe organisateur',
        html: `<form id="admin-login-form" class="stack" autocomplete="off">
          <p>Cette action est réservée à l'organisateur du challenge.</p>
          <div class="field"><label for="admin-pw">Mot de passe</label><input id="admin-pw" type="password" name="password" autocomplete="current-password" required></div>
          <div class="form-error" id="admin-pw-error"></div>
        </form>`,
        onCancel: cancel,
        actions: [
          { label: 'Annuler', className: 'btn-ghost', onClick: ({ close }) => { close(); cancel(); } },
          { label: 'Se connecter', className: 'btn-primary', onClick: ({ modal }) => modal.querySelector('#admin-login-form').requestSubmit() },
        ],
      });
      const form = m.el.querySelector('#admin-login-form');
      const errEl = m.el.querySelector('#admin-pw-error');
      const submitBtn = m.el.querySelector('.btn-primary');
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const pw = form.password.value;
        if (!pw) { errEl.textContent = 'Entre le mot de passe.'; return; }
        App.setLoading(submitBtn, true);
        try {
          const ok = await App.verifyAdmin(pw);
          if (!ok) throw new Error('Mot de passe incorrect.');
          m.close();
          resolve(pw);
        } catch (err) {
          errEl.textContent = err.message || 'Mot de passe incorrect.';
          form.password.select();
        } finally {
          App.setLoading(submitBtn, false);
        }
      });
    });
  };

  App.verifyAdmin = async function (pw) {
    try {
      await App.api('/api/admin/login', { method: 'POST', admin: pw, body: {} });
      App.setAdminPassword(pw);
      return true;
    } catch (e) {
      if (e.status === 401 || e.status === 403) return false;
      throw e;
    }
  };

  /* Exécute une action admin : demande le mot de passe au besoin, réessaie une fois si 401. */
  App.adminAction = async function (fn) {
    try {
      await App.requireAdmin();
      try {
        return await fn();
      } catch (e) {
        if (e && e.status === 401) {
          App.setAdminPassword('');
          App.toast('Mot de passe refusé, réessaie.', { type: 'error' });
          await App.requireAdmin();
          return await fn();
        }
        throw e;
      }
    } catch (e) {
      if (e && e.cancelled) return undefined;
      throw e;
    }
  };

  /* ------------------------------------------------------------------ */
  /* Notifications navigateur                                             */
  /* ------------------------------------------------------------------ */
  const notifBtn = $('#btn-notifications');
  const notifSupported = 'Notification' in window;

  function renderNotifButton() {
    if (!notifBtn) return;
    if (!notifSupported) { notifBtn.hidden = true; return; }
    notifBtn.hidden = false;
    const p = Notification.permission;
    if (p === 'granted') {
      notifBtn.textContent = '🔔 Notifications actives';
      notifBtn.title = 'Tu seras prévenu quand un joueur lance une partie';
      notifBtn.disabled = true;
    } else if (p === 'denied') {
      notifBtn.textContent = '🔕 Notifications bloquées';
      notifBtn.title = 'Autorise les notifications dans les réglages du navigateur';
      notifBtn.disabled = true;
    } else {
      notifBtn.textContent = '🔔 Activer les notifications';
      notifBtn.title = 'Être prévenu quand un joueur lance une partie';
      notifBtn.disabled = false;
    }
  }

  if (notifBtn && notifSupported) {
    notifBtn.addEventListener('click', async () => {
      try {
        const result = await Notification.requestPermission();
        renderNotifButton();
        if (result === 'granted') {
          App.toast('Notifications activées : tu seras prévenu dès qu’un joueur lance une partie.', { type: 'success' });
          App.notify('Notifications activées', 'Tu seras prévenu quand un joueur lance une partie.');
        } else if (result === 'denied') {
          App.toast('Notifications refusées par le navigateur.', { type: 'warning' });
        }
      } catch (e) { /* navigateur sans promesse */ renderNotifButton(); }
    });
  }
  renderNotifButton();

  App.notify = function (title, bodyText, tag) {
    if (!notifSupported || Notification.permission !== 'granted') return;
    try {
      const n = new Notification(title, { body: bodyText || '', icon: '/static/img/favicon.svg', tag: tag || undefined, silent: false });
      n.onclick = () => { window.focus(); n.close(); };
      setTimeout(() => n.close(), 12000);
    } catch (e) { /* ex. mobile sans service worker */ }
  };

  /* ------------------------------------------------------------------ */
  /* Compteur « en game » dans la nav                                     */
  /* ------------------------------------------------------------------ */
  const livePill = $('#nav-live');
  App.setLiveCount = function (n) {
    if (!livePill) return;
    n = Math.max(0, n | 0);
    livePill.dataset.count = String(n);
    livePill.classList.toggle('is-live', n > 0);
    const txt = livePill.querySelector('.pill-live-text');
    if (txt) txt.textContent = n === 0 ? 'Personne en game' : `${n} en game`;
  };
  App.refreshLiveCount = App.debounce(async () => {
    try {
      const data = await App.api('/api/live');
      App.setLiveCount((data && data.live && data.live.length) || 0);
    } catch (e) { /* silencieux : indicateur secondaire */ }
  }, 400);
  App.refreshLiveCount();

  /* ------------------------------------------------------------------ */
  /* Événements : le site demande les nouveautés toutes les 5 s           */
  /* (pas de connexion permanente : ne bloque jamais les autres requêtes  */
  /*  du navigateur et passe par n'importe quel tunnel ou proxy)          */
  /* ------------------------------------------------------------------ */
  const POLL_MS = 5000;
  const POLL_HIDDEN_MS = 20000;
  const listeners = {}; // type → [fn]
  let lastEventId = null; // null = premier appel (on ne rejoue pas l'historique)
  let pollTimer = null;
  let polling = false;
  let started = false;
  let needHello = true;

  App.onEvent = function (type, fn) {
    (listeners[type] = listeners[type] || []).push(fn);
    return () => { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); };
  };

  function dispatch(type, data) {
    if (!data || typeof data !== 'object') data = {};
    (listeners[type] || []).forEach((fn) => { try { fn(data, type); } catch (e) { console.error('[events]', type, e); } });
    (listeners['*'] || []).forEach((fn) => { try { fn(data, type); } catch (e) { console.error('[events]', type, e); } });
  }

  function schedule(delay) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pollOnce, delay !== undefined ? delay : (document.hidden ? POLL_HIDDEN_MS : POLL_MS));
  }

  async function pollOnce() {
    if (polling) return;
    polling = true;
    try {
      const url = lastEventId === null ? '/api/events/recent' : `/api/events/recent?since=${encodeURIComponent(lastEventId)}`;
      const r = await App.api(url);
      if (!App.connected) { App.connected = true; document.dispatchEvent(new CustomEvent('pekin:connected')); }
      if (needHello) { needHello = false; dispatch('hello', r.hello || {}); }
      (r.events || []).forEach((ev) => {
        if (typeof ev.id === 'number' && (lastEventId === null || ev.id > lastEventId)) lastEventId = ev.id;
        if (ev.type && ev.type !== 'ping') dispatch(ev.type, ev.data || {});
      });
      if (typeof r.last_id === 'number' && (lastEventId === null || r.last_id > lastEventId)) lastEventId = r.last_id;
      if (lastEventId === null) lastEventId = 0;
    } catch (e) {
      if (App.connected) { App.connected = false; document.dispatchEvent(new CustomEvent('pekin:disconnected')); }
      needHello = true; // à la reconnexion, les pages rechargent leurs données
    } finally {
      polling = false;
      schedule();
    }
  }

  App.connectEvents = function (handlers) {
    if (handlers) Object.keys(handlers).forEach((t) => App.onEvent(t, handlers[t]));
    if (!started) {
      started = true;
      pollOnce();
      document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(0); });
    }
    return true;
  };

  /* Une page peut couper temporairement les toasts globaux d'un type. */
  App.mutedEvents = new Set();
  const muted = (type) => App.mutedEvents.has(type);

  /* Libellés communs pour les toasts et notifications. */
  function who(d) {
    return d.display_name || d.player_name || (d.player && d.player.display_name) || 'Un joueur';
  }
  function teamOf(d) {
    return d.team_name || (d.team && d.team.name) || '';
  }
  function champOf(d) {
    return d.champion_name || d.champion || (d.live && d.live.champion_name) || '';
  }

  App.onEvent('live_start', (d) => {
    const team = teamOf(d);
    const champ = champOf(d);
    const msg = `🔴 ${who(d)}${team ? ` (${team})` : ''} lance une partie${champ ? ` — ${champ}` : ''}`;
    App.toast(msg, { type: 'live', timeout: 7000 });
    App.notify(msg, 'Va l’encourager (ou le troll) !', `live-${d.player_id || who(d)}`);
    App.refreshLiveCount();
  });
  App.onEvent('live_end', (d) => {
    App.toast(`⏹ ${who(d)} a terminé sa partie`, { type: 'info', timeout: 4000 });
    App.refreshLiveCount();
  });
  App.onEvent('match_recorded', (d) => {
    if (d.is_remake) return;
    const lp = d.lp_change;
    const win = d.win === true || d.win === 'true';
    const champ = champOf(d);
    const lpTxt = lp === null || lp === undefined ? '' : ` ${App.formatLp(lp)}`;
    const msg = win ? `✅ ${who(d)} gagne${lpTxt}` : `❌ ${who(d)} perd${lpTxt}`;
    App.toast(`${msg}${champ ? ` (${champ})` : ''}`, { type: win ? 'success' : 'error', timeout: 6000 });
    App.notify(msg, champ ? `Partie enregistrée — ${champ}` : 'Partie enregistrée', `match-${d.match_id || Date.now()}`);
  });
  App.onEvent('draw_done', () => {
    if (muted('draw_done')) return;
    App.toast('🤝 Les duos sont formés !', { type: 'info' });
    App.notify('🤝 Les duos sont formés !', 'Découvre ton partenaire sur la page Duos.');
  });
  App.onEvent('challenge_started', () => {
    App.toast('🚀 Le challenge a commencé, bonne chance !', { type: 'success' });
    App.notify('🚀 Le challenge a commencé !', 'Que le meilleur duo gagne.');
  });
  App.onEvent('challenge_finished', () => App.toast('🏁 Le challenge est terminé.', { type: 'info' }));

  /* Après une mise à jour du site, le serveur redémarre : quand la page se reconnecte au flux
     SSE et voit (via `hello`) que la version des fichiers a changé, elle se recharge d'elle-même. */
  let reloadScheduled = false;
  App.onEvent('hello', (d) => {
    if (reloadScheduled || !d || !d.asset_version || !App.assetVersion) return;
    if (d.asset_version !== App.assetVersion) {
      reloadScheduled = true;
      App.toast('⬆️ Nouvelle version du site, rechargement…', { type: 'info', timeout: 3000 });
      setTimeout(() => location.reload(), 1200);
    }
  });
  App.onEvent('challenge_reset', () => App.toast('♻️ Le challenge a été réinitialisé.', { type: 'warning' }));
  App.onEvent('player_registered', (d) => App.toast(`👋 ${who(d)} a rejoint le challenge`, { type: 'info', timeout: 4000 }));
  App.onEvent('player_linked', (d) => App.toast(`🔗 ${who(d)} a lié son compte`, { type: 'success', timeout: 4000 }));

  App.connectEvents();

  /* Pastille « mis à jour » optionnelle : les pages l'alimentent. */
  App.ready = (fn) => (document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn());
})();
