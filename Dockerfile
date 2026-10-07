# Pékin Express LoL — image pour un VPS ou un hébergeur de conteneurs (Railway, Fly.io, Render…).
# La configuration passe par les variables d'environnement (voir .env.example) ;
# la base SQLite vit dans /app/data : à monter sur un volume persistant.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////app/data/tracker.db \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY players.yaml .

RUN mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 8000

# `--proxy-headers` : derrière le reverse proxy de l'hébergeur (HTTPS)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
