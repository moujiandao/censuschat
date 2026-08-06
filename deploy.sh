#!/usr/bin/env bash
# Bring the app up on the EC2 host. Run from the repo root on the deploy
# host, with .env already populated (see .env.example) and
# SNOWFLAKE_PRIVATE_KEY_HOST_PATH pointing at the Snowflake key on the host.
#
# Starts ONLY the `app` service: that host runs Caddy natively (it also
# serves memory.brianmar.com), so the bundled caddy service would fight it
# for ports 80/443. docker-compose.override.yml publishes the app on
# 127.0.0.1:8000, which the host's /etc/caddy/Caddyfile already proxies to.
# See docs/decisions.md D-016.
set -euo pipefail
cd "$(dirname "$0")"

echo "Pulling latest code..."
git pull --ff-only

echo "Building and starting the app container..."
docker compose up -d --build app

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
