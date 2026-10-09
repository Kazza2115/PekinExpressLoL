"""Ouverture du navigateur au lancement : lien fixe GitHub dès qu'il renvoie vers le nouveau tunnel."""

from __future__ import annotations

from app import open_site

PORTAL = "https://kazza2115.github.io/PekinExpressLoL/"
NEW = "https://new-tunnel.trycloudflare.com"
OLD = "https://old-tunnel.trycloudflare.com"


def state(*, enabled=True, published=None, waiting=None, error=None, public=NEW, source="tunnel"):
    return {
        "portal": {"enabled": enabled, "portal_url": PORTAL, "published_url": published, "waiting": waiting, "error": error},
        "public_url": {"url": public, "source": source},
    }


def run(states, **kwargs):
    """Rejoue une suite d'états (None = serveur pas encore prêt) avec une horloge factice."""
    seq = list(states)
    clock = {"t": 0.0}

    def fetch():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def sleep(seconds):
        clock["t"] += seconds

    return open_site.choose_url(fetch=fetch, sleep=sleep, clock=lambda: clock["t"], **kwargs), clock["t"]


def test_opens_github_link_once_new_address_is_published():
    url, waited = run([
        None, None,  # serveur en démarrage
        state(published=None),  # pas encore de tunnel publié
        state(published=NEW, waiting="Le tunnel ne répond pas encore"),
        state(published=OLD, public=NEW),  # GitHub a encore l'ancienne adresse
        state(published=NEW),
    ])
    assert url == PORTAL and waited > 0


def test_without_github_token_opens_the_tunnel_address():
    url, _ = run([state(enabled=False, public="http://localhost:8000", source="local"), state(enabled=False)])
    assert url == NEW


def test_token_refused_falls_back_to_tunnel():
    assert run([state(error="Jeton GitHub refusé")])[0] == NEW


def test_timeouts_and_local_mode():
    # Lien fixe jamais prêt : l'adresse du tunnel au bout du délai
    url, waited = run([state(published=None)])
    assert url == NEW and waited >= open_site.PORTAL_TIMEOUT_S
    # Ni tunnel ni lien fixe : adresse locale
    assert run([state(enabled=False, public="http://localhost:8000", source="local")])[0] == open_site.LOCAL_URL
    # Serveur qui ne démarre pas, ou AUTO_TUNNEL=false
    assert run([None])[0] == open_site.LOCAL_URL
    assert run([state(published=NEW)], local_only=True)[0] == open_site.LOCAL_URL


def test_main_opens_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(open_site, "choose_url", lambda local_only=False: "LOCAL" if local_only else PORTAL)
    monkeypatch.setattr(open_site.webbrowser, "open", opened.append)
    open_site.main([])
    open_site.main(["--local"])
    assert opened == [PORTAL, "LOCAL"]
