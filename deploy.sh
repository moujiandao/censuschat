#!/usr/bin/env bash
# Bring the stack up on the EC2 host. Run from the repo root on the deploy
# host, with .env already populated (see .env.example) and
# SNOWFLAKE_PRIVATE_KEY_HOST_PATH / BASIC_AUTH_HASH set per
# docker-compose.yml's comments.
set -euo pipefail
cd "$(dirname "$0")"

echo "Pulling latest code..."
git pull --ff-only

echo "Building and starting containers..."
docker compose up -d --build

echo "Waiting for app health check..."
HEALTH_CHECK='import urllib.request; urllib.request.urlopen("http://localhost:8000/api/health", timeout=2)'
for i in $(seq 1 30); do
	if docker compose exec -T app python -c "$HEALTH_CHECK" >/dev/null 2>&1; then
		echo "App healthy."
		docker compose ps
		exit 0
	fi
	sleep 2
done

echo "App failed health check after 60s" >&2
docker compose logs app
exit 1
