#!/bin/sh
set -eu

docker compose down
docker image rm -f email_assistant:latest >/dev/null 2>&1 || true
docker compose build --no-cache
docker compose up -d
