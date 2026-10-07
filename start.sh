#!/usr/bin/env bash
# Pékin Express LoL — lancement en une commande (macOS / Linux) : ./start.sh
# Premier lancement : crée le venv, installe les dépendances, prépare .env.
# Ensuite : démarre le site sur http://localhost:8000 (accessible sur le réseau local).
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 est introuvable : installe-le (https://python.org) puis relance ./start.sh"
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Première installation : création de l'environnement Python…"
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    echo "Installation des dépendances…"
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo
    echo "  Le fichier .env vient d'être créé : colle ta clé Riot (RIOT_API_KEY=RGAPI-…)"
    echo "  et choisis ADMIN_PASSWORD, puis relance ./start.sh. Sans clé : mode démo."
    echo
    exit 0
fi

echo
echo "  Site : http://localhost:8000   (autres machines du réseau : http://<IP de ce PC>:8000)"
echo "  Laisse ce terminal ouvert pendant le challenge. Ctrl+C pour arrêter."
echo
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
