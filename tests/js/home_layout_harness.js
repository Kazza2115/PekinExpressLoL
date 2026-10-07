// Harnais node pour tests/test_front_events.py : rend l'accueil (home.js) avec un DOM factice et un
// `fetch` qui imite GET /api/state dans le statut de challenge demandé, puis rapporte ce que la mise
// en page montre ou cache (formulaire d'inscription, « Lier mon compte », conseil sous « Les joueurs »).
// Sortie : un JSON sur stdout. Usage : node home_layout_harness.js <app.js> <home.js> <status>
'use strict';
const path = require('path');

const [APP_JS, HOME_JS, STATUS] = process.argv.slice(2);
if (!APP_JS || !HOME_JS || !STATUS) {
  console.error('usage: node home_layout_harness.js <app.js> <home.js> <registration|drawn|running|finished>');
  process.exit(2);
}

/* --- Pas de minuterie réelle : le polling (app.js) et le rafraîchissement périodique (home.js)
   ne doivent pas garder node en vie ; seul le premier chargement nous intéresse. --- */
const realSetImmediate = setImmediate;
async function flush() { for (let i = 0; i < 10; i++) await new Promise((r) => realSetImmediate(r)); }
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.setInterval = () => 0;
global.clearInterval = () => {};

/* --- DOM factice : les éléments que home.js récupère par id, avec `hidden`, `innerHTML`, classList --- */
const noop = () => {};
function fakeEl(id) {
  const set = new Set();
  return {
    id, dataset: {}, style: {}, hidden: false, textContent: '', innerHTML: '', disabled: false, children: [],
    classes: set,
    classList: {
      toggle: (c, force) => { const on = force === undefined ? !set.has(c) : !!force; if (on) set.add(c); else set.delete(c); return on; },
      add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c),
    },
    addEventListener: noop, removeEventListener: noop, querySelector: () => null, querySelectorAll: () => [],
    appendChild: noop, remove: noop, reset: noop, focus: noop,
  };
}
const IDS = [
  'hero-name', 'hero-status', 'hero-count', 'hero-cta', 'register-card', 'register-form', 'register-error',
  'register-submit', 'btn-demo-fill', 'demo-card', 'duos-section', 'duos-grid', 'duos-hint', 'slots',
  'players-hint', 'home-side', 'home-root',
];
const byId = {};
IDS.forEach((id) => { byId[id] = fakeEl(id); });
byId['home-root'].classList.add('home-layout'); // comme dans le gabarit home.html

global.window = global;
global.document = Object.assign(fakeEl('document'), {
  body: { dataset: { page: 'home', demo: 'true', gamesPerDay: '10', asset: 'v-test' } },
  readyState: 'complete', dispatchEvent: noop, createElement: () => fakeEl('new'), activeElement: null, hidden: false,
  querySelector: (sel) => (sel.startsWith('#') ? byId[sel.slice(1)] || null : null),
});
global.sessionStorage = { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = v; }, removeItem(k) { delete this._s[k]; } };
global.matchMedia = () => ({ matches: false });
global.CustomEvent = class { constructor(t) { this.type = t; } };
global.location = { reload: noop, href: '/' };
class FakeNotification { static requestPermission() { return Promise.resolve('denied'); } }
FakeNotification.permission = 'default';
global.Notification = FakeNotification;

/* --- Faux serveur : GET /api/state (même forme que routes_api) et GET /api/events/recent ---
   Un duo tiré (Mike & Léa) et un retardataire inscrit sans compte : c'est lui qui doit voir
   « Lier mon compte » tant que l'API accepte la liaison (registration + drawn). */
function player(id, name, linked, teamId) {
  return {
    id, display_name: name, riot_id: linked ? `${name}#EUW` : null, is_linked: linked, link_error: null,
    active: true, team_id: teamId, icon_url: null, tier: linked ? 'GOLD' : null, rank: linked ? 'II' : null,
    lp: linked ? 40 : null, rank_label: linked ? 'Gold II · 40 LP' : 'Non classé', rank_color: null, rank_emblem_url: null,
  };
}
const STATE = {
  challenge: { name: 'Pékin Express LoL', status: STATUS, start_at: null, end_at: null },
  players: [player(1, 'Mike', true, 10), player(2, 'Léa', true, 10), player(3, 'Retardataire', false, null)],
  teams: [{ id: 10, name: 'Duo Rouge', color: '#ef4444', slot: 1, player_ids: [1, 2], window_start: null, window_end: null }],
  demo_players: [],
};
const requested = [];
global.fetch = async (url) => {
  const u = new URL(String(url), 'http://pekin.test');
  requested.push(u.pathname);
  let body = {};
  if (u.pathname === '/api/state') body = STATE;
  else if (u.pathname === '/api/events/recent') {
    body = { events: [], last_id: 0, hello: { challenge_status: STATUS, live_count: 0, asset_version: 'v-test' }, asset_version: 'v-test' };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
};

require(path.resolve(APP_JS));
require(path.resolve(HOME_JS)); // appelle load() → GET /api/state → render()

(async () => {
  await flush();
  const slots = byId.slots.innerHTML;
  process.stdout.write(JSON.stringify({
    status: STATUS,
    requested,
    registerHidden: byId['register-card'].hidden,
    sideHidden: byId['home-side'].hidden,
    demoHidden: byId['demo-card'].hidden,
    homeLayout: byId['home-root'].classes.has('home-layout'),
    playersHint: byId['players-hint'].textContent,
    linkForms: (slots.match(/data-link-form=/g) || []).length,
    slotsHtml: slots,
    ctaHtml: byId['hero-cta'].innerHTML,
    countHtml: byId['hero-count'].innerHTML,
  }) + '\n');
})().catch((e) => { console.error((e && e.stack) || e); process.exit(1); });
