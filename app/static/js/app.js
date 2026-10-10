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

  /* `startAt` : challenge démarré mais début programmé pas encore atteint → « ⏳ Démarre sam. 10 oct. 09:00 ». */
  App.statusChip = function (status, startAt) {
    if (status === 'running' && startAt) {
      const t = Date.parse(startAt);
      if (!isNaN(t) && t > Date.now()) {
        const label = new Date(t).toLocaleString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
        return `<span class="chip chip-gold chip-lg" title="Les parties comptent à partir de cette heure">⏳ Démarre ${esc(label)}</span>`;
      }
    }
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

  /* Présence : identifiant du navigateur (le même pour tous ses onglets), de l'onglet, et le
     joueur que la personne dit être (« Qui es-tu ? », facultatif, jamais une autorisation). */
  const CID_KEY = 'pekin_cid';
  const ME_KEY = 'pekin_me';
  function storage(kind) {
    try { const s = kind === 'local' ? window.localStorage : window.sessionStorage; return s || null; } catch (e) { return null; }
  }
  function randomId() {
    try {
      const c = window.crypto;
      if (c && typeof c.randomUUID === 'function') return c.randomUUID().replace(/-/g, '');
      if (c && typeof c.getRandomValues === 'function') {
        return Array.from(c.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
      }
    } catch (e) { /* page non sécurisée (http://IP-locale) : repli ci-dessous */ }
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  }
  function storedId(kind, key) {
    const s = storage(kind);
    try {
      const known = s && s.getItem(key);
      if (known && /^[A-Za-z0-9_-]{8,64}$/.test(known)) return known;
      const fresh = randomId();
      if (s) s.setItem(key, fresh);
      return fresh;
    } catch (e) { return randomId(); }
  }
  App.clientId = storedId('local', CID_KEY);
  // Onglet : propre à chaque chargement de page (un onglet dupliqué ou ouvert par window.open
  // copie le sessionStorage : il ne doit pas partager l'identifiant de l'autre onglet)
  App.tabId = randomId();
  let memMe; // repli quand le navigateur refuse le stockage : l'identité tient le temps de la page
  App.getMe = function () {
    try {
      const s = storage('local');
      if (s) {
        const me = JSON.parse(s.getItem(ME_KEY) || 'null');
        return me && Number.isInteger(me.id) && me.id > 0 ? me : null;
      }
    } catch (e) { /* stockage bloqué : repli en mémoire */ }
    return memMe || null;
  };
  App.setMe = function (player) {
    memMe = player && player.id ? { id: player.id, name: player.name || '' } : null;
    const s = storage('local');
    try {
      if (s && memMe) s.setItem(ME_KEY, JSON.stringify(memMe));
      else if (s) s.removeItem(ME_KEY);
    } catch (e) { /* stockage indisponible : l'identité ne tient que le temps de la page */ }
    if (App.pollNow) App.pollNow();
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
    backdrop.innerHTML = `<div class="modal${opts.className ? ` ${esc(opts.className)}` : ''}" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <h3 id="modal-title">${esc(opts.title || '')}</h3>
      <div class="modal-body">${opts.html || ''}</div>
      <div class="modal-actions"></div>
    </div>`;
    const actions = backdrop.querySelector('.modal-actions');
    const previouslyFocused = document.activeElement;
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      backdrop.remove();
      document.removeEventListener('keydown', onKey);
      if (opts.onClose) { try { opts.onClose(); } catch (e) { console.error(e); } }
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

  /* ------------------------------------------------------------------ */
  /* Tableau des scores d'une partie (style op.gg)                        */
  /* ------------------------------------------------------------------ */
  const scoreboardCache = {};
  App.loadScoreboard = function (matchId) {
    if (!scoreboardCache[matchId]) {
      scoreboardCache[matchId] = App.api(`/api/matches/${encodeURIComponent(matchId)}`).then((r) => r.match)
        .catch((e) => { delete scoreboardCache[matchId]; throw e; });
    }
    return scoreboardCache[matchId];
  };

  const fmtK = (v) => (v === null || v === undefined ? '—' : (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1).replace('.', ',')} k` : String(v)));
  const signedK = (v) => (v === null || v === undefined ? '' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmtK(Math.abs(v))}`);

  function runeIcon(url, name, cls) {
    return `<span class="sb-rune ${cls || ''}${url ? '' : ' is-broken'}" title="${esc(name || 'Rune')}">${url ? `<img src="${esc(url)}" alt="" loading="lazy" onerror="this.parentNode.classList.add('is-broken');this.remove()">` : ''}</span>`;
  }

  function scoreboardRow(p, focusId) {
    const me = focusId && p.player_id === focusId;
    const name = p.player_id
      ? `<a class="sb-name is-ours" href="/player/${p.player_id}" title="${esc(p.riot_name)}${p.riot_tag ? `#${esc(p.riot_tag)}` : ''}">${esc(p.display_name)}</a>`
      : `<span class="sb-name" title="${esc(p.riot_name)}${p.riot_tag ? `#${esc(p.riot_tag)}` : ''}">${esc(p.riot_name)}</span>`;
    const tags = [
      p.badge ? `<span class="sb-badge ${p.badge === 'MVP' ? 'is-mvp' : 'is-ace'}" title="${p.badge === 'MVP' ? 'Meilleur joueur de l’équipe gagnante' : 'Meilleur joueur de l’équipe perdante'}">${p.badge}</span>` : '',
      p.multi_kill_label ? `<span class="sb-tag">${esc(p.multi_kill_label)}</span>` : '',
      p.first_blood ? '<span class="sb-tag" title="Premier sang">🩸</span>' : '',
    ].join('');
    const ratio = p.perfect_kda ? 'Parfait' : `${Number(p.kda).toFixed(2).replace('.', ',')}:1`;
    const laneDiff = p.gold_diff_lane === null || p.gold_diff_lane === undefined ? ''
      : `<span class="sb-diff ${p.gold_diff_lane > 0 ? 'pos' : p.gold_diff_lane < 0 ? 'neg' : ''}" title="Écart d'or avec l'adversaire de la même voie">${signedK(p.gold_diff_lane)}</span>`;
    return `<tr class="${p.player_id ? 'is-ours' : ''}${me ? ' is-me' : ''}">
      <td class="sb-player">
        <div class="sb-player-in">
          ${App.champIcon({ name: p.champion_name || '?', src: p.champion_icon_url, size: 'md', level: p.champ_level, title: p.champion_name })}
          <span class="sb-stack">${App.spellIcons(p.spell_urls)}</span>
          <span class="sb-stack sb-runes">${runeIcon(p.keystone_url, p.keystone, 'is-keystone')}${runeIcon(p.secondary_style_url, p.secondary_style, 'is-style')}</span>
          <span class="sb-who">${name}<span class="sb-sub">${p.position_label ? esc(p.position_label) : ''}${p.riot_tag && p.player_id ? ` · ${esc(p.riot_name)}#${esc(p.riot_tag)}` : ''}</span>${tags ? `<span class="sb-tags">${tags}</span>` : ''}</span>
        </div>
      </td>
      <td class="sb-kda"><strong class="tnum">${p.kills}/<span class="neg">${p.deaths}</span>/${p.assists}</strong><span class="sb-sub tnum">${ratio}${p.kill_participation !== null && p.kill_participation !== undefined ? ` · KP ${Math.round(p.kill_participation)} %` : ''}</span></td>
      <td class="sb-dmg"><span class="tnum">${App.formatNumber(p.damage)}</span><span class="sb-bar"><span style="width:${Math.max(2, p.damage_pct_of_max || 0)}%"></span></span><span class="sb-sub tnum" title="Dégâts subis">subis ${App.formatNumber(p.damage_taken)}</span></td>
      <td class="sb-gold"><span class="tnum">${fmtK(p.gold)}</span>${laneDiff}</td>
      <td class="sb-cs"><span class="tnum">${p.cs}</span><span class="sb-sub tnum">${p.cs_per_min !== null && p.cs_per_min !== undefined ? `${String(p.cs_per_min).replace('.', ',')}/min` : ''}</span></td>
      <td class="sb-vision"><span class="tnum">${p.vision_score}</span><span class="sb-sub tnum" title="Balises posées / détruites · contrôle">${p.wards_placed}/${p.wards_killed}${p.control_wards ? ` · ${p.control_wards} ctrl` : ''}</span></td>
      <td class="sb-items">${App.itemRow(p.item_urls, { size: 'sm' })}</td>
    </tr>`;
  }

  App.scoreboardHtml = function (m, opts) {
    opts = opts || {};
    const teams = m.teams || [];
    const blue = teams.find((t) => t.side === 'blue') || teams[0] || {};
    const red = teams.find((t) => t.side === 'red') || teams[1] || {};
    const pct = (a, b) => (a + b > 0 ? Math.round((a / (a + b)) * 100) : 50);
    const objectives = (t) => {
      const o = t.objectives || {};
      return [['tower', '🗼', 'Tourelles'], ['inhibitor', '🏚️', 'Inhibiteurs'], ['dragon', '🐉', 'Dragons'], ['baron', '👾', 'Barons'], ['riftHerald', '🦀', 'Hérauts'], ['horde', '🐛', 'Larves du Néant']]
        .filter(([key]) => o[key] !== undefined)
        .map(([key, icon, label]) => `<span title="${label}">${icon} ${o[key]}</span>`).join('');
    };
    const bans = (t) => (t.bans || []).length
      ? `<span class="sb-bans" title="Champions bannis">${t.bans.map((b) => App.champIcon({ name: b.champion_name || '?', src: b.champion_icon_url, size: 'xs', title: b.champion_name || `Champion ${b.champion_id}` })).join('')}</span>` : '';
    const teamBlock = (t) => `<section class="sb-team side-${esc(t.side || '')} ${t.win ? 'is-win' : 'is-loss'}">
      <header class="sb-team-head">
        <strong>${t.win ? 'Victoire' : 'Défaite'}</strong><span class="muted">${esc(t.side_label || '')}</span>
        <span class="tnum">${t.kills}/${t.deaths}/${t.assists}</span><span class="tnum" title="Or total">💰 ${fmtK(t.gold)}</span>
        <span class="sb-obj">${objectives(t)}</span>${bans(t)}
      </header>
      <div class="sb-scroll"><table class="sb-table">
        <thead><tr><th>Joueur</th><th>KDA</th><th>Dégâts</th><th>Or</th><th>CS</th><th>Vision</th><th>Objets</th></tr></thead>
        <tbody>${(t.players || []).map((p) => scoreboardRow(p, opts.focusPlayerId)).join('')}</tbody>
      </table></div>
    </section>`;
    const diff = m.gold_diff || 0;
    return `<div class="sb">
      <div class="sb-meta">
        <span class="chip">${esc(m.queue_label || 'Partie')}</span>
        <span class="tnum">${App.formatDuration(m.duration_s)}</span>
        ${m.game_start ? `<span>${esc(App.formatDateTime(m.game_start))}</span>` : ''}
        ${m.patch ? `<span class="muted">patch ${esc(m.patch)}</span>` : ''}
        ${m.remake ? '<span class="chip">Remake</span>' : ''}
      </div>
      <div class="sb-compare">
        <div class="sb-cmp"><span class="lbl">Kills</span><div class="sb-split"><span class="b" style="width:${pct(blue.kills || 0, red.kills || 0)}%">${blue.kills || 0}</span><span class="r">${red.kills || 0}</span></div></div>
        <div class="sb-cmp"><span class="lbl">Or</span><div class="sb-split"><span class="b" style="width:${pct(blue.gold || 0, red.gold || 0)}%">${fmtK(blue.gold || 0)}</span><span class="r">${fmtK(red.gold || 0)}</span></div><span class="sb-diff ${diff > 0 ? 'blue' : diff < 0 ? 'red' : ''}">${diff ? `${diff > 0 ? 'Bleue' : 'Rouge'} ${signedK(Math.abs(diff))}` : 'égalité'}</span></div>
        <div class="sb-cmp"><span class="lbl">Dégâts</span><div class="sb-split"><span class="b" style="width:${pct(blue.damage || 0, red.damage || 0)}%">${fmtK(blue.damage || 0)}</span><span class="r">${fmtK(red.damage || 0)}</span></div></div>
      </div>
      ${teamBlock(blue)}
      ${teamBlock(red)}
    </div>`;
  };

  /* Ouvre le tableau des scores d'une partie dans une grande fenêtre. */
  App.openMatch = function (matchId, focusPlayerId) {
    const modal = App.openModal({
      title: 'Tableau des scores',
      className: 'modal-wide',
      html: '<div class="sb-loading muted">Chargement…</div>',
      actions: [{ label: 'Fermer', className: 'btn-ghost', onClick: ({ close }) => close() }],
    });
    App.loadScoreboard(matchId)
      .then((m) => { const body = modal.el.querySelector('.modal-body'); if (body) body.innerHTML = App.scoreboardHtml(m, { focusPlayerId }); })
      .catch((e) => { const body = modal.el.querySelector('.modal-body'); if (body) body.innerHTML = `<div class="empty">${esc(e.message || 'Détail indisponible.')}</div>`; });
    return modal;
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
  // Android refuse `new Notification()` : il faut passer par un service worker (`/sw.js`, sans cache)
  let swRegistration = null;
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js').then((reg) => { swRegistration = reg; }).catch(() => {});
  }

  function renderNotifButton() {
    if (!notifBtn) return;
    notifBtn.hidden = false;
    notifBtn.disabled = false;
    if (!notifSupported) {
      notifBtn.textContent = '🔕 Notifications indisponibles';
      notifBtn.title = window.isSecureContext ? 'Ce navigateur ne gère pas les notifications' : 'Les notifications exigent une adresse https:// ou 127.0.0.1';
      return;
    }
    const p = Notification.permission;
    if (p === 'granted') {
      notifBtn.textContent = '🔔 Notifications actives';
      notifBtn.title = 'Clique pour recevoir une notification de test';
    } else if (p === 'denied') {
      notifBtn.textContent = '🔕 Notifications bloquées';
      notifBtn.title = 'Autorise les notifications dans les réglages du navigateur';
    } else {
      notifBtn.textContent = '🔔 Activer les notifications';
      notifBtn.title = 'Être prévenu quand un joueur lance une partie';
    }
  }

  /* Son d'alerte (deux bips) : les navigateurs ne jouent du son qu'après un premier clic sur la page. */
  let audioCtx = null;
  function unlockAudio() {
    try {
      if (!audioCtx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (Ctx) audioCtx = new Ctx();
      }
      if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
    } catch (e) { /* pas de son */ }
  }
  document.addEventListener('pointerdown', unlockAudio, { once: true });
  document.addEventListener('keydown', unlockAudio, { once: true });
  App.playAlert = function () {
    try {
      unlockAudio();
      if (!audioCtx || audioCtx.state !== 'running') return;
      const t0 = audioCtx.currentTime;
      [[880, 0], [1320, 0.18]].forEach(([freq, delay]) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t0 + delay);
        gain.gain.exponentialRampToValueAtTime(0.25, t0 + delay + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + delay + 0.28);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(t0 + delay);
        osc.stop(t0 + delay + 0.3);
      });
    } catch (e) { /* pas de son */ }
  };

  /* Onglet en arrière-plan : le titre clignote jusqu'à ce qu'on revienne sur la page. */
  const baseTitle = document.title;
  let titleTimer = null;
  App.flashTitle = function (text) {
    if (!document.hidden) return;
    clearInterval(titleTimer);
    let on = false;
    titleTimer = setInterval(() => { on = !on; document.title = on ? text : baseTitle; }, 1000);
  };
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && titleTimer) { clearInterval(titleTimer); titleTimer = null; document.title = baseTitle; }
  });

  function legacyNotify(title, options) {
    try {
      const n = new Notification(title, options);
      n.onclick = () => { window.focus(); n.close(); };
      return true;
    } catch (e) { return false; } // ex. Android sans service worker
  }

  /* `opts.requireInteraction` : la notification reste affichée jusqu'au clic (parties lancées).
     Jamais de fermeture automatique : sous Windows, une notification retenue pendant une partie
     en plein écran doit rester dans le centre de notifications. */
  App.notify = function (title, bodyText, tag, opts) {
    if (!notifSupported || Notification.permission !== 'granted') return false;
    const options = {
      body: bodyText || '',
      icon: '/static/img/favicon.svg',
      badge: '/static/img/favicon.svg',
      requireInteraction: !!(opts && opts.requireInteraction),
      silent: false,
      data: { url: location.href },
    };
    if (tag) { options.tag = tag; options.renotify = true; }
    if (swRegistration && swRegistration.showNotification) {
      swRegistration.showNotification(title, options).catch(() => legacyNotify(title, options));
      return true;
    }
    return legacyNotify(title, options);
  };

  function sendTestNotification() {
    App.notify('🔔 Test Pékin Express', 'Les notifications fonctionnent sur cet appareil.', 'pekin-test');
    App.playAlert();
    App.toast('Notification de test envoyée. Rien ne s’affiche ? Sous Windows, coupe « Ne pas déranger » (il bloque tout pendant une partie en plein écran) et autorise ton navigateur dans Paramètres › Système › Notifications.', { type: 'info', timeout: 14000 });
  }

  if (notifBtn) {
    notifBtn.addEventListener('click', async () => {
      unlockAudio();
      if (!notifSupported) {
        App.toast(window.isSecureContext
          ? 'Ce navigateur ne gère pas les notifications : utilise Chrome, Edge ou Firefox, ou le salon Discord.'
          : 'Notifications impossibles sur cette adresse : ouvre le site via http://127.0.0.1:8000 sur le PC qui l’héberge, ou via le lien https:// Cloudflare.', { type: 'warning', timeout: 10000 });
        return;
      }
      if (Notification.permission === 'granted') { sendTestNotification(); return; }
      if (Notification.permission === 'denied') {
        App.toast('Notifications bloquées : clique sur le cadenas à gauche de l’adresse, autorise les notifications, puis recharge la page.', { type: 'warning', timeout: 10000 });
        return;
      }
      try {
        const result = await Notification.requestPermission();
        renderNotifButton();
        if (result === 'granted') sendTestNotification();
        else if (result === 'denied') App.toast('Notifications refusées par le navigateur.', { type: 'warning' });
      } catch (e) { /* navigateur sans promesse */ renderNotifButton(); }
    });
  }
  renderNotifButton();

  /* ------------------------------------------------------------------ */
  /* Parties en cours : compteur de la nav, bandeau « En direct »,        */
  /* tableau des 10 joueurs (App.liveBoardHtml) et fenêtre de la partie   */
  /* ------------------------------------------------------------------ */
  const livePill = $('#nav-live');
  App.setLiveCount = function (n) {
    if (!livePill) return;
    n = Math.max(0, n | 0);
    livePill.dataset.count = String(n);
    livePill.classList.toggle('is-live', n > 0);
    const txt = livePill.querySelector('.pill-live-text');
    const label = n === 0 ? 'Personne en game' : `${n} en game`;
    if (txt) txt.textContent = label;
    livePill.setAttribute('aria-label', label);
  };

  /* Début (ms) d'une partie : `game_start` sinon maintenant − durée écoulée. */
  App.liveStartMs = function (g) {
    const t = g && g.game_start ? Date.parse(g.game_start) : NaN;
    return !isNaN(t) && t > 0 ? t : Date.now() - ((g && g.elapsed_s) || 0) * 1000;
  };

  /* État partagé : `items` (un par joueur en partie), `games` (une par partie, avec le tableau). */
  App.live = { loaded: false, items: [], games: [], byPlayer: new Map(), byGame: new Map() };
  App.setLive = function (data) {
    const items = (data && Array.isArray(data.live)) ? data.live : [];
    const games = (data && Array.isArray(data.games)) ? data.games : [];
    App.live = {
      loaded: true,
      items,
      games,
      byPlayer: new Map(items.map((i) => [i.player_id, i])),
      byGame: new Map(games.map((g) => [String(g.game_id), g])),
    };
    App.setLiveCount(items.length);
    renderLiveStrip();
    ensureTicker();
    try { document.dispatchEvent(new CustomEvent('pekin:live', { detail: App.live })); } catch (e) { /* vieux navigateur */ }
  };
  App.refreshLive = App.debounce(async () => {
    try { App.setLive(await App.api('/api/live')); } catch (e) { /* silencieux : indicateur secondaire */ }
  }, 400);
  App.refreshLiveCount = App.refreshLive; // ancien nom
  App.refreshLive();

  /* Durées qui défilent ([data-elapsed-start]) : une seule minuterie, active seulement quand une
     partie est en cours (le bandeau, le tableau et la fenêtre en ont besoin sur toutes les pages). */
  let tickerOn = false;
  App.tickElapsed = function () {
    const now = Date.now();
    $$('[data-elapsed-start]').forEach((el) => {
      const start = parseInt(el.dataset.elapsedStart, 10);
      if (!isNaN(start)) el.textContent = App.formatDuration((now - start) / 1000);
    });
  };
  function ensureTicker() {
    if (tickerOn || !App.live.items.length) return;
    tickerOn = true;
    const loop = () => {
      if (!App.live.items.length) { tickerOn = false; return; }
      if (!document.hidden) App.tickElapsed();
      setTimeout(loop, 1000);
    };
    setTimeout(loop, 1000);
  }

  /* Titre d'une partie : « Mike & Léa (Duo Rouge) », « Mike contre Hugo »… */
  App.liveTitle = function (g) {
    const cps = (g && g.challenge_players) || [];
    if (!cps.length) return 'Partie en cours';
    const sides = {};
    cps.forEach((cp) => { const k = cp.side || 'x'; (sides[k] = sides[k] || []).push(cp); });
    const group = (list) => {
      const names = list.map((cp) => esc(cp.display_name || 'Joueur')).join(' & ');
      const duos = [...new Set(list.map((cp) => cp.team_name).filter(Boolean))];
      return `${names}${duos.length === 1 ? ` <span class="muted">(${esc(duos[0])})</span>` : ''}`;
    };
    return Object.values(sides).map(group).join(' <span class="lb-vs">contre</span> ');
  };

  /* Tableau d'une partie en cours : deux équipes de 5 (champion, sorts, runes, Riot ID), les
     joueurs du challenge surlignés à la couleur de leur duo, bans, durée qui défile. */
  App.liveBoardHtml = function (g, opts) {
    opts = opts || {};
    if (!g) return `<div class="lb lb-empty muted">${esc(opts.emptyText || 'Partie terminée.')}</div>`;
    const startMs = App.liveStartMs(g);
    const riot = (p) => (p.riot_name ? `${p.riot_name}${p.riot_tag ? `#${p.riot_tag}` : ''}` : (p.bot ? 'Bot' : 'Joueur masqué'));
    const row = (p) => {
      const ours = !!p.is_challenge;
      const me = ours && opts.focusPlayerId && p.player_id === opts.focusPlayerId;
      const name = ours
        ? `<a class="lb-name" href="/player/${encodeURIComponent(p.player_id)}">${esc(p.display_name)}</a>`
        : `<span class="lb-name">${esc(riot(p))}</span>`;
      const sub = ours
        ? `${p.rank_label ? `<span class="rank" style="--rank-color:${esc(p.rank_color || App.rankColor(null))}">${esc(p.rank_label)}</span>` : ''}<span class="lb-riot">${esc(riot(p))}</span>`
        : `<span>${esc(p.champion_name || '')}</span>`;
      return `<li class="lb-row${ours ? ' is-ours' : ''}${me ? ' is-me' : ''}"${ours && p.team_color ? ` style="--team-color:${esc(p.team_color)}"` : ''}>
        ${App.champIcon({ name: p.champion_name || '?', src: p.champion_icon_url, size: 'sm', title: p.champion_name })}
        <span class="sb-stack">${App.spellIcons(p.spell_urls)}</span>
        <span class="sb-stack sb-runes">${runeIcon(p.keystone_url, p.keystone, 'is-keystone')}${runeIcon(p.secondary_style_url, p.secondary_style, 'is-style')}</span>
        <span class="lb-who">${name}<span class="lb-sub">${sub}</span></span>
        ${ours && p.team_name ? `<span class="chip chip-team lb-duo" style="--team-color:${esc(p.team_color || '#e5b64d')}" title="${esc(p.team_name)}"><span class="swatch"></span>${esc(String(p.team_name).replace(/^Duo\s+/i, ''))}</span>` : ''}
      </li>`;
    };
    const team = (t) => `<section class="lb-team side-${esc(t.side)}${t.has_challenge_player ? ' has-ours' : ''}">
        <header class="lb-team-head"><strong>${esc(t.side_label || 'Équipe')}</strong>${(t.bans || []).length ? `<span class="lb-bans" title="Champions bannis">${t.bans.map((b) => App.champIcon({ name: b.champion_name || '?', src: b.champion_icon_url, size: 'xs', title: `Banni : ${b.champion_name || '?'}`, className: 'is-ban' })).join('')}</span>` : ''}</header>
        <ul class="lb-list">${(t.players || []).map(row).join('')}</ul>
      </section>`;
    const teams = Array.isArray(g.teams) ? g.teams : [];
    const body = teams.length
      ? `<div class="lb-teams">${teams.map(team).join('')}</div>`
      : `<div class="lb-pending muted">Composition de la partie pas encore disponible : elle s'affiche à la prochaine vérification.</div>`;
    return `<div class="lb" data-game="${esc(g.game_id)}">
      <div class="lb-meta">
        <span class="badge-live"><span class="dot"></span>En direct</span>
        <span class="chip">${esc(g.queue_label || App.queueLabel(g.queue_id, g.game_mode))}</span>
        <span class="tnum lb-time" title="${g.loading ? 'Écran de chargement' : 'Durée de la partie'}" data-elapsed-start="${startMs}">${App.formatDuration((Date.now() - startMs) / 1000)}</span>
        ${opts.showTitle === false ? '' : `<span class="lb-title">${App.liveTitle(g)}</span>`}
      </div>
      ${body}
    </div>`;
  };

  /* Badge « En game » cliquable : ouvre le tableau de la partie (ou y descend sur le classement). */
  App.liveBadgeHtml = function (liveInfo, opts) {
    if (!liveInfo) return '';
    opts = opts || {};
    const startMs = App.liveStartMs(liveInfo);
    const icon = !opts.compact && liveInfo.champion_icon_url ? App.champIcon({ name: liveInfo.champion_name, src: liveInfo.champion_icon_url, size: 'xs', title: liveInfo.champion_name }) : '';
    const label = opts.compact ? 'Live' : `En game${liveInfo.champion_name ? ` <span class="detail detail-champ">· ${esc(liveInfo.champion_name)}</span>` : ''} <span class="detail tnum" data-elapsed-start="${startMs}">${App.formatDuration((Date.now() - startMs) / 1000)}</span>`;
    const gameAttr = liveInfo.game_id !== undefined && liveInfo.game_id !== null ? ` data-live-game="${esc(liveInfo.game_id)}"` : '';
    return `<button type="button" class="badge-live is-action"${gameAttr} data-live-player="${esc(opts.playerId || '')}" title="Voir la partie en direct">${icon}<span class="dot"></span>${label}</button>`;
  };

  /* Fenêtre « Partie en cours » : se met à jour toute seule, et annonce la fin de la partie. */
  App.openLiveBoard = function (gameId, focusPlayerId) {
    const key = String(gameId);
    const html = () => {
      const g = App.live.byGame.get(key);
      return g
        ? App.liveBoardHtml(g, { focusPlayerId })
        : `<div class="lb lb-empty"><p><strong>Partie terminée.</strong></p><p class="muted">Le tableau des scores complet apparaît dans « Dernières parties » dès que Riot publie le résultat (quelques minutes).</p></div>`;
    };
    let sig = '';
    const modal = App.openModal({
      title: 'Partie en cours',
      className: 'modal-wide modal-live',
      html: html(),
      actions: [{ label: 'Fermer', className: 'btn-ghost', onClick: ({ close }) => close() }],
      onClose: () => document.removeEventListener('pekin:live', update),
    });
    function update() {
      const g = App.live.byGame.get(key);
      const next = g ? JSON.stringify([g.game_start, (g.teams || []).map((t) => (t.players || []).map((p) => p.champion_id))]) : 'end';
      if (next === sig) return;
      sig = next;
      const body = modal.el.querySelector('.modal-body');
      if (body) body.innerHTML = html();
      const title = modal.el.querySelector('#modal-title');
      if (title) title.textContent = g ? 'Partie en cours' : 'Partie terminée';
    }
    sig = (() => { const g = App.live.byGame.get(key); return g ? JSON.stringify([g.game_start, (g.teams || []).map((t) => (t.players || []).map((p) => p.champion_id))]) : 'end'; })();
    document.addEventListener('pekin:live', update);
    if (!App.live.byGame.has(key)) App.refreshLive();
    return modal;
  };

  /* Clic sur un badge ou une puce « en direct », sur n'importe quelle page. */
  document.addEventListener('click', (e) => {
    const target = e.target && e.target.closest ? e.target.closest('[data-live-game]') : null;
    if (!target) return;
    const link = e.target.closest('a[href]');
    if (link && link !== target && target.contains(link)) return; // lien interne : on le suit
    e.preventDefault();
    e.stopPropagation();
    const gameId = target.dataset.liveGame;
    const anchor = document.getElementById(`live-game-${gameId}`);
    if (anchor) {
      document.dispatchEvent(new CustomEvent('pekin:live-focus', { detail: { gameId } })); // déplie la carte
      anchor.scrollIntoView({ behavior: App.reducedMotion ? 'auto' : 'smooth', block: 'start' });
      anchor.classList.remove('is-flash');
      void anchor.offsetWidth; // relance l'animation
      anchor.classList.add('is-flash');
      if (!anchor.hasAttribute('tabindex')) anchor.setAttribute('tabindex', '-1');
      try { anchor.focus({ preventScroll: true }); } catch (err) { /* vieux navigateur */ }
      return;
    }
    App.openLiveBoard(gameId, parseInt(target.dataset.livePlayer, 10) || null);
  });

  /* Hauteur de l'en-tête collant (nav sur 1 à 3 lignes + bandeau) → --header-h, utilisée par
     scroll-padding-top : les ancres (#duo-3, tableaux en direct) ne finissent plus dessous. */
  const header = $('header.nav');
  if (header && typeof window.ResizeObserver === 'function') {
    new window.ResizeObserver(() => {
      document.documentElement.style.setProperty('--header-h', `${header.offsetHeight}px`);
    }).observe(header);
  }

  /* Bandeau « En direct » sous la nav (toutes les pages) : une puce par partie. */
  const liveStrip = $('#live-strip');
  const liveStripItems = $('#live-strip-items');
  if (liveStripItems) {
    liveStripItems.addEventListener('wheel', (e) => {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX) || liveStripItems.scrollWidth <= liveStripItems.clientWidth) return;
      e.preventDefault();
      liveStripItems.scrollLeft += e.deltaY;
    }, { passive: false });
  }
  let stripSig = '';
  function renderLiveStrip() {
    if (!liveStrip || !liveStripItems) return;
    const games = App.live.games;
    liveStrip.hidden = !games.length;
    const sig = JSON.stringify(games.map((g) => [g.game_id, g.game_start, (g.challenge_players || []).map((cp) => [cp.player_id, cp.champion_name])]));
    if (sig === stripSig) return;
    stripSig = sig;
    liveStripItems.innerHTML = games.map((g) => {
      const cps = g.challenge_players || [];
      const color = (cps[0] && cps[0].team_color) || '#f05a5a';
      const icons = cps.map((cp) => {
        const row = (g.teams || []).flatMap((t) => t.players || []).find((p) => p.player_id === cp.player_id);
        const live = App.live.byPlayer.get(cp.player_id);
        return App.champIcon({ name: cp.champion_name || '?', src: (row && row.champion_icon_url) || (live && live.champion_icon_url), size: 'xs', title: `${cp.display_name} · ${cp.champion_name || ''}` });
      }).join('');
      const bySide = {};
      cps.forEach((cp) => { const k = cp.side || 'x'; (bySide[k] = bySide[k] || []).push(esc(cp.display_name)); });
      const names = Object.values(bySide).map((list) => list.join(' & ')).join(' vs ');
      return `<button type="button" class="live-chip" data-live-game="${esc(g.game_id)}" style="--team-color:${esc(color)}" title="Voir la partie">
        <span class="live-chip-icons">${icons}</span><span class="live-chip-name">${names || 'Partie'}</span>
        <span class="live-chip-time tnum" data-elapsed-start="${App.liveStartMs(g)}">${App.formatDuration(g.elapsed_s || 0)}</span>
      </button>`;
    }).join('');
  }

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
      const params = [];
      if (lastEventId !== null) params.push(`since=${encodeURIComponent(lastEventId)}`);
      // Signe de présence (qui a le site ouvert) : navigateur, onglet, page, joueur choisi, visibilité
      const me = App.getMe();
      params.push(`cid=${encodeURIComponent(App.clientId)}`, `tab=${encodeURIComponent(App.tabId)}`, `page=${encodeURIComponent(App.page)}`, `vis=${document.hidden ? 0 : 1}`);
      if (me) params.push(`me=${encodeURIComponent(me.id)}`);
      const r = await App.api(`/api/events/recent?${params.join('&')}`);
      if (!App.connected) { App.connected = true; document.dispatchEvent(new CustomEvent('pekin:connected')); }
      if (needHello) { needHello = false; dispatch('hello', r.hello || {}); }
      if (r.presence) applyPresence(r.presence, me, r.me);
      (r.events || []).forEach((ev) => {
        if (typeof ev.id === 'number' && (lastEventId === null || ev.id > lastEventId)) lastEventId = ev.id;
        if (ev.type && ev.type !== 'ping') dispatch(ev.type, ev.data || {});
      });
      if (typeof r.last_id === 'number' && (lastEventId === null || r.last_id > lastEventId)) lastEventId = r.last_id;
      // Le serveur a redémarré (compteur d'événements reparti à 1) : on repart de son dernier id,
      // sinon `since` resterait figé et plus aucun toast / notification n'arriverait.
      if (typeof r.last_id === 'number' && lastEventId !== null && r.last_id < lastEventId) lastEventId = r.last_id;
      if (lastEventId === null) lastEventId = 0;
    } catch (e) {
      if (App.connected) { App.connected = false; document.dispatchEvent(new CustomEvent('pekin:disconnected')); }
      needHello = true; // à la reconnexion, les pages rechargent leurs données
    } finally {
      polling = false;
      schedule();
    }
  }

  App.pollNow = () => schedule(0);

  App.connectEvents = function (handlers) {
    if (handlers) Object.keys(handlers).forEach((t) => App.onEvent(t, handlers[t]));
    if (!started) {
      started = true;
      pollOnce();
      document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(0); });
      if (typeof window.addEventListener === 'function') {
        // Identité changée dans un autre onglet : on le signale tout de suite
        window.addEventListener('storage', (e) => { if (e.key === ME_KEY) schedule(0); });
        // Onglet fermé : on disparaît de la liste sans attendre l'expiration
        window.addEventListener('pagehide', () => {
          try {
            if (navigator.sendBeacon) navigator.sendBeacon('/api/presence/leave', new Blob([JSON.stringify({ cid: App.clientId, tab: App.tabId })], { type: 'application/json' }));
          } catch (e) { /* tant pis : expiration au bout de 30 s */ }
        });
        window.addEventListener('pageshow', (e) => { if (e.persisted) schedule(0); });
      }
    }
    return true;
  };

  /* ------------------------------------------------------------------ */
  /* Personnes connectées : pastille « N en ligne » et fenêtre détaillée  */
  /* ------------------------------------------------------------------ */
  const PAGE_LABELS = {
    home: 'sur l’accueil', duos: 'sur la page Duos', dashboard: 'sur le classement', rankings: 'sur les rangs',
    player: 'sur une fiche joueur', admin: 'dans l’admin', other: 'sur le site',
  };
  const onlinePill = $('#nav-online');
  let presenceSig = '';
  let onlineModal = null;
  let playersForWho = null;
  App.presence = null;

  function applyPresence(p, sentMe, me) {
    // Joueur choisi refusé par le serveur (supprimé, désactivé) ou numéro repris par un autre joueur
    // après une réinitialisation (nom différent) : on oublie l'identité plutôt que d'usurper l'autre
    if (sentMe && (!me || me.display_name !== sentMe.name)) {
      App.setMe(null);
      App.toast('Ton choix « Qui es-tu ? » a été oublié (joueur introuvable) : choisis à nouveau.', { type: 'info', timeout: 6000 });
    }
    const sig = JSON.stringify([p.online, p.anonymous, (p.players || []).map((x) => [x.player_id, x.active, x.page, x.in_game])]);
    if (sig === presenceSig) return;
    presenceSig = sig;
    App.presence = p;
    renderOnlinePill();
    if (onlineModal) renderOnlineModal();
    dispatch('presence', p);
  }

  function renderOnlinePill() {
    if (!onlinePill || !App.presence) return;
    const n = App.presence.online || 0;
    onlinePill.hidden = n < 1;
    onlinePill.dataset.count = String(n);
    const txt = onlinePill.querySelector('.pill-online-text');
    if (txt) txt.textContent = `${n} en ligne`;
    onlinePill.setAttribute('aria-label', `${n} en ligne : voir qui`);
    const names = (App.presence.players || []).map((x) => x.display_name);
    const anon = App.presence.anonymous || 0;
    onlinePill.title = `Sur le site en ce moment : ${[...names, anon ? `${anon} visiteur${anon > 1 ? 's' : ''}` : ''].filter(Boolean).join(', ') || 'personne'}`;
  }

  function peopleHtml() {
    const p = App.presence || { players: [], anonymous: 0, online: 0 };
    const me = App.getMe();
    const items = (p.players || []).map((x) => `<li class="online-item${x.active ? '' : ' is-away'}${me && me.id === x.player_id ? ' is-me' : ''}">
        ${App.avatar({ name: x.display_name, src: x.icon_url, color: x.team_color || undefined, size: 'sm', className: x.in_game ? 'is-live' : '' })}
        <a class="online-name" href="/player/${encodeURIComponent(x.player_id)}">${esc(x.display_name)}</a>
        ${x.in_game ? '<span class="badge-live"><span class="dot"></span>En game</span>' : ''}
        <span class="online-where">${x.active ? esc(PAGE_LABELS[x.page] || PAGE_LABELS.other) : 'en arrière-plan'}</span>
      </li>`).join('');
    const anon = p.anonymous || 0;
    // Le visiteur anonyme qui regarde la liste fait partie du compte : on le dit
    const others = Math.max(0, anon - (me ? 0 : 1));
    const visitors = (n) => `${n} visiteur${n > 1 ? 's' : ''} anonyme${n > 1 ? 's' : ''}`;
    const anonLine = me
      ? (anon ? `${items ? '+ ' : ''}${visitors(anon)}` : '')
      : `Toi (anonyme)${others ? ` et ${others > 1 ? `${others} autres` : '1 autre'} visiteur${others > 1 ? 's' : ''}` : ''}`;
    return `${items ? `<ul class="online-list">${items}</ul>` : '<p class="muted">Aucun joueur identifié pour l’instant.</p>'}
      ${anonLine ? `<p class="online-anon">${anonLine}</p>` : ''}`;
  }

  function whoHtml() {
    const me = App.getMe();
    const options = (playersForWho || []).map((x) => `<option value="${esc(x.id)}"${me && me.id === x.id ? ' selected' : ''}>${esc(x.display_name)}</option>`).join('');
    return `<label for="online-who-select">Qui es-tu ?</label>
      <select id="online-who-select" class="input"><option value="">Spectateur (anonyme)</option>${options}</select>`;
  }

  function onlineHtml() {
    return `<div class="online-people" aria-live="polite">${peopleHtml()}</div>
      <div class="online-who">${whoHtml()}</div>
      <p class="muted" style="font-size:12px;margin-top:8px">Ton nom apparaît alors dans cette liste pour les autres. Rien d'autre n'est enregistré.</p>`;
  }

  /* Seule la liste est redessinée (la liste déroulante « Qui es-tu ? » n'est jamais remplacée
     pendant qu'on s'en sert), en gardant le focus sur le même lien s'il y était. */
  function renderOnlineModal(options) {
    if (!onlineModal) return;
    const wrap = onlineModal.el.querySelector('.online-people');
    if (wrap) {
      const active = document.activeElement;
      const href = active && wrap.contains(active) ? active.getAttribute('href') : null;
      wrap.innerHTML = peopleHtml();
      if (href) {
        const safeHref = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(href) : href.replace(/"/g, '');
        const again = wrap.querySelector(`a[href="${safeHref}"]`) || onlineModal.el.querySelector('.modal-actions button');
        if (again) again.focus();
      }
    }
    if (options && options.who) {
      const who = onlineModal.el.querySelector('.online-who');
      if (who && !who.contains(document.activeElement)) who.innerHTML = whoHtml();
    }
  }

  async function openOnline() {
    onlineModal = App.openModal({
      title: 'Sur le site en ce moment',
      html: onlineHtml(),
      actions: [{ label: 'Fermer', className: 'btn-primary', onClick: ({ close }) => close() }],
      onClose: () => { onlineModal = null; },
    });
    if (!playersForWho) {
      try {
        const st = await App.api('/api/state');
        playersForWho = ((st && st.players) || []).filter((x) => x.active !== false).map((x) => ({ id: x.id, display_name: x.display_name }));
        renderOnlineModal({ who: true });
      } catch (e) { /* la liste reste sans choix d'identité */ }
    }
  }
  if (onlinePill) {
    onlinePill.addEventListener('click', openOnline);
    document.addEventListener('change', (e) => {
      if (!e.target || e.target.id !== 'online-who-select') return;
      const id = parseInt(e.target.value, 10);
      const chosen = (playersForWho || []).find((x) => x.id === id);
      App.setMe(chosen ? { id: chosen.id, name: chosen.display_name } : null);
      renderOnlineModal(); // « (toi) » tout de suite ; la liste complète suit au prochain échange
      App.toast(chosen ? `Tu apparais maintenant comme ${chosen.display_name}.` : 'Tu apparais maintenant comme spectateur.', { type: 'success', timeout: 3000 });
    });
    document.addEventListener('pekin:disconnected', () => onlinePill.classList.add('is-offline'));
    document.addEventListener('pekin:connected', () => onlinePill.classList.remove('is-offline'));
  }

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
    App.toast(msg, { type: 'live', timeout: 15000 });
    App.notify(msg, 'Va l’encourager (ou le troll) !', `live-${d.player_id || who(d)}`, { requireInteraction: true });
    App.playAlert();
    App.flashTitle(`🔴 ${who(d)} en game`);
    App.refreshLive();
  });
  App.onEvent('live_end', (d) => {
    App.toast(`⏹ ${who(d)} a terminé sa partie`, { type: 'info', timeout: 4000 });
    App.refreshLive();
  });
  // Début réel de la partie (après l'écran de chargement) ou composition arrivée entre-temps
  App.onEvent('poll_done', () => { if (App.live.items.length) App.refreshLive(); });
  App.onEvent('live_update', () => App.refreshLive());
  App.onEvent('match_recorded', (d) => {
    if (d.is_remake) return;
    const lp = d.lp_change;
    const win = d.win === true || d.win === 'true';
    const champ = champOf(d);
    const lpTxt = lp === null || lp === undefined ? '' : ` ${App.formatLp(lp)}`;
    const quota = d.outside_window
      ? ' · hors des heures du challenge, ne compte pas'
      : (d.over_quota ? ` · hors quota${d.day_game_number ? ` (${d.day_game_number}e partie du jour)` : ''}, ne compte pas` : '');
    const msg = win ? `✅ ${who(d)} gagne${lpTxt}${quota}` : `❌ ${who(d)} perd${lpTxt}${quota}`;
    const ignored = d.over_quota || d.outside_window;
    App.toast(`${msg}${champ ? ` (${champ})` : ''}`, { type: ignored ? 'warning' : (win ? 'success' : 'error'), timeout: ignored ? 10000 : 6000 });
    App.notify(msg, champ ? `Partie enregistrée — ${champ}` : 'Partie enregistrée', `match-${d.match_id || Date.now()}`);
  });
  App.onEvent('draw_done', () => {
    if (muted('draw_done')) return;
    App.toast('🤝 Les duos sont formés !', { type: 'info' });
    App.notify('🤝 Les duos sont formés !', 'Découvre ton partenaire sur la page Duos.');
  });
  App.onEvent('challenge_started', (d) => {
    const start = d && d.challenge && d.challenge.start_at ? Date.parse(d.challenge.start_at) : NaN;
    if (!isNaN(start) && start > Date.now()) {
      const when = new Date(start).toLocaleString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
      App.toast(`🚀 Tout est prêt : le challenge commence le ${when}.`, { type: 'success', timeout: 9000 });
      App.notify('🚀 Tout est prêt !', `Le challenge commence le ${when}.`);
      return;
    }
    App.toast('🚀 Le challenge a commencé, bonne chance !', { type: 'success' });
    App.notify('🚀 Le challenge a commencé !', 'Que le meilleur duo gagne.');
  });
  App.onEvent('challenge_finished', () => App.toast('🏁 Le challenge est terminé.', { type: 'info' }));
  App.onEvent('challenge_begins', () => {
    App.toast('🚀 C’est parti : le challenge commence ! Bonne chance à tous les duos.', { type: 'success', timeout: 12000 });
    App.notify('🚀 C’est parti !', 'Le challenge commence : bonne chance à tous les duos.', 'challenge-begins');
  });
  App.onEvent('joker_used', (d) => {
    const msg = `🃏 ${d.team_name || 'Un duo'} active son joker${d.display_name ? ` (par ${d.display_name})` : ''} : ${d.limit || ''} parties comptées aujourd'hui`;
    App.toast(msg, { type: 'live', timeout: 10000 });
    App.notify(msg, 'Seules les parties terminées à partir de maintenant en profitent.', `joker-${d.team_id || ''}`);
  });
  App.onEvent('joker_cancelled', () => App.toast('🃏 Un joker a été annulé par l’organisateur.', { type: 'info' }));

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
