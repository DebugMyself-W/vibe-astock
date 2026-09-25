#!/bin/sh
set -eu

cd /app

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

pip install -r requirements.txt

if [ -f frontend/package-lock.json ]; then
  cd frontend
  npm ci
  npm run build
  cd /app
fi

exec "$@"
