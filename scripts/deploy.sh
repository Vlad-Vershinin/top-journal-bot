#!/usr/bin/env bash
set -Eeuo pipefail

deploy_path="${1:?deploy path is required}"
expected_sha="${2:?expected commit SHA is required}"

cd "$deploy_path"

if [[ ! -f .env ]]; then
  echo "Deployment aborted: $deploy_path/.env does not exist" >&2
  exit 1
fi

echo "Fetching commit $expected_sha"
git fetch --prune origin main
git checkout main
git merge --ff-only origin/main

actual_sha="$(git rev-parse HEAD)"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  echo "Deployment aborted: expected $expected_sha, got $actual_sha" >&2
  exit 1
fi

echo "Building and starting container"
docker compose up -d --build --remove-orphans

echo "Waiting for Telegram polling to start"
for _ in $(seq 1 30); do
  if [[ "$(docker inspect --format '{{.State.Running}}' top-journal-bot 2>/dev/null || true)" != "true" ]]; then
    echo "Container stopped during startup" >&2
    docker compose logs --no-color --tail 100 bot >&2
    exit 1
  fi

  if docker compose logs --no-color bot 2>&1 | grep -Fq "Application started"; then
    echo "Deployment successful: $actual_sha"
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "Bot did not start polling within 60 seconds" >&2
docker compose logs --no-color --tail 100 bot >&2
exit 1

