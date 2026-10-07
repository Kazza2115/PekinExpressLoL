// Harnais node pour tests/test_front_events.py : charge app.js avec un DOM factice, un `fetch`
// qui imite GET /api/events/recent (même contrat que app/api/routes_api.py) et des minuteries
// pilotées à la main, puis simule un redémarrage du serveur (bus d'événements reparti à 1).
// Sortie : un JSON sur stdout. Usage : node poll_restart_harness.js <chemin de app.js>
'use strict';
const path = require('path');

const APP_JS = process.argv[2];
if (!APP_JS) { console.error('usage: node poll_restart_harness.js <app.js>'); process.exit(2); }

/* --- Minuteries pilotées à la main (app.js interroge le serveur toutes les 5 s) --- */
const realSetImmediate = setImmediate;
const timers = new Map();
let nextTimer = 1;
global.setTimeout = (fn, ms) => { const id = nextTimer++; timers.set(id, { fn, ms: ms || 0 }); return id; };
global.clearTimeout = (id) => { timers.delete(id); };
async function flush() { for (let i = 0; i < 5; i++) await new Promise((r) => realSetImmediate(r)); }
async function tick() {
  // Exécute les minuteries en attente (une seule fois), puis laisse les promesses se régler.
  const due = Array.from(timers.values());
  timers.clear();
  for (const t of due) t.fn();
  await flush();
}

/* --- DOM factice : juste ce que app.js touche au chargement --- */
const noop = () => {};
const fakeEl = () => ({
  dataset: {}, classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  addEventListener: noop, querySelector: () => null, querySelectorAll: () => [], appendChild: noop,
  remove: noop, children: [], style: {}, hidden: false, textContent: '', innerHTML: '',
});
global.window = global;
global.document = Object.assign(fakeEl(), {
  body: { dataset: { page: 'duos', demo: 'false', gamesPerDay: '10', asset: 'v-test' } },
  readyState: 'complete', dispatchEvent: noop, createElement: fakeEl, activeElement: null, hidden: false,
});
global.sessionStorage = { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = v; }, removeItem(k) { delete this._s[k]; } };
global.matchMedia = () => ({ matches: false });
global.CustomEvent = class { constructor(t) { this.type = t; } };
let reloaded = false;
global.location = { reload: () => { reloaded = true; } };
global.focus = noop;

/* --- Notifications navigateur : autorisées, on note celles qui sont créées --- */
const notifs = [];
class FakeNotification {
  constructor(title, opts) { notifs.push({ title, body: (opts && opts.body) || '' }); }
  close() {}
  static requestPermission() { return Promise.resolve('granted'); }
}
FakeNotification.permission = 'granted';
global.Notification = FakeNotification;

/* --- Faux serveur : même contrat que GET /api/events/recent --- */
const ASSET = 'v-test';
let history = []; // événements du processus serveur courant
let counter = 0;
function publish(type, data) { counter += 1; history.push({ id: counter, type, ts: '2026-01-01T00:00:00+00:00', data: data || {} }); }
function restartServer() { history = []; counter = 0; } // le bus en mémoire repart à 1 à chaque démarrage
const polls = []; // valeur de `since` à chaque interrogation (null = sans paramètre)
global.fetch = async (url) => {
  const u = new URL(String(url), 'http://pekin.test');
  let body = {};
  if (u.pathname === '/api/events/recent') {
    const raw = u.searchParams.get('since');
    const since = raw === null ? null : parseInt(raw, 10);
    polls.push(since);
    const lastId = history.length ? history[history.length - 1].id : 0;
    const events = since === null ? [] : history.filter((e) => e.id > since);
    body = { events, last_id: lastId, hello: { challenge_status: 'running', live_count: 0, asset_version: ASSET }, asset_version: ASSET };
  } else if (u.pathname === '/api/live') {
    body = { live: [] };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
};

/* --- Scénario --- */
for (let i = 0; i < 230; i++) publish('poll_done', {}); // le serveur tourne depuis des heures
require(path.resolve(APP_JS)); // appelle App.connectEvents() → première interrogation
const App = global.App;
const toasts = [];
const origToast = App.toast;
App.toast = function (msg, opts) { toasts.push(msg); return origToast.apply(this, arguments); };
const dispatched = [];
App.onEvent('*', (d, type) => dispatched.push(type));

(async () => {
  await flush(); // 1er appel : sans `since`, le curseur se cale sur 230
  await tick(); // 2e appel : since=230 → rien de neuf
  restartServer(); // relance de PekinExpress.bat / --reload / plantage / reboot
  for (let i = 0; i < 3; i++) publish('poll_done', {});
  await tick(); // 3e appel : since=230, le nouveau processus répond last_id=3
  publish('live_start', { player_id: 1, display_name: 'Alice', team_name: 'Duo Rouge', champion_name: 'Ahri' });
  publish('rank_changed', { player_id: 1, display_name: 'Alice' });
  await tick(); // 4e appel : doit suivre le nouveau compteur (since=3) et recevoir le live_start
  await tick(); // 5e appel : since=5
  process.stdout.write(JSON.stringify({ polls, toasts, notifs, dispatched, reloaded, server_last_id: counter }) + '\n');
})().catch((e) => { console.error((e && e.stack) || e); process.exit(1); });
