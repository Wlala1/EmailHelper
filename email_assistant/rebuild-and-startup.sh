#!/bin/sh
set -eu

docker-compose down
docker-compose build
docker-compose up -d

# Clean up the old, untagged images left behind after the new build
docker image prune -f
