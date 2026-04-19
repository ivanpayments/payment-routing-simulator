#!/usr/bin/env bash
# Deploy Project 2 routing simulator to the droplet with pre- and post-deploy gates.
#
# Pre-deploy gates (ABORT on failure):
#   1. pytest suite green locally
#   2. CSV pattern validator: 139/139 in-scope MET, 0 unexplained contradictions
#
# Post-deploy gates (WARN but keep deploy):
#   3. /router/health returns 200
#   4. 500-sample live compliance run shows 0 unexplained failures
#
# Override:
#   SKIP_PRE_DEPLOY=1 ./scripts/deploy.sh    # skip gates 1+2 (not recommended)
#   SKIP_POST_DEPLOY=1 ./scripts/deploy.sh   # skip gates 3+4

set -euo pipefail

DROPLET="${DROPLET:-root@209.38.71.25}"
REMOTE_PATH="${REMOTE_PATH:-/opt/payment-router}"
HEALTH_URL="${HEALTH_URL:-https://ivanantonov.com/router/health}"
SKIP_PRE_DEPLOY="${SKIP_PRE_DEPLOY:-0}"
SKIP_POST_DEPLOY="${SKIP_POST_DEPLOY:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/Scripts/python.exe}"
if [ ! -x "$PYTHON" ]; then PYTHON="${PYTHON%/Scripts/python.exe}/bin/python"; fi

cd "$PROJECT_ROOT"

if [ "$SKIP_PRE_DEPLOY" != "1" ]; then
  echo "==> [pre-deploy 1/2] pytest"
  "$PYTHON" -m pytest -q || { echo "ABORT: pytest failed"; exit 1; }

  echo "==> [pre-deploy 2/2] CSV pattern validator"
  if [ -f "Claude files/_agent_a_v2_validate.py" ] && [ -f "Claude files/routing_transactions.csv" ]; then
    "$PYTHON" "Claude files/_agent_a_v2_validate.py" | tee /tmp/p2-csv-validate.out
    if grep -qE 'NOT MET.*[1-9]' /tmp/p2-csv-validate.out; then
      echo "ABORT: CSV has in-scope NOT MET patterns"
      exit 1
    fi
  else
    echo "WARN: CSV validator or dataset not found locally — skipping"
  fi
fi

echo "==> tar-over-ssh project to $DROPLET:$REMOTE_PATH"
ssh "$DROPLET" "mkdir -p $REMOTE_PATH"
tar --exclude='Claude files' \
    --exclude='./.venv' \
    --exclude='./.git' \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='payment_router.db' \
    --exclude='.env' \
    --exclude='.env.local' \
    --exclude='docker-compose.override.yml' \
    -czf - . | ssh "$DROPLET" "cd $REMOTE_PATH && tar -xzf -"

echo "==> build + up on droplet"
ssh "$DROPLET" "cd $REMOTE_PATH && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"

if [ "$SKIP_POST_DEPLOY" != "1" ]; then
  echo "==> [post-deploy 1/2] wait for health"
  healthy=0
  for i in {1..30}; do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      echo "   healthy at $HEALTH_URL"
      healthy=1
      break
    fi
    sleep 2
  done
  if [ "$healthy" != "1" ]; then
    echo "ERROR: health check failed after 60s — inspect with:"
    echo "  ssh $DROPLET 'cd $REMOTE_PATH && docker compose logs --tail=100'"
    exit 1
  fi

  echo "==> [post-deploy 2/2] 500-sample live compliance (optional)"
  if [ -f "scripts/validate_api_compliance.py" ] && [ -n "${ROUTER_API_KEY:-}" ]; then
    "$PYTHON" scripts/validate_api_compliance.py --base-url "${HEALTH_URL%/health}" --samples 500 --api-key "$ROUTER_API_KEY" || echo "WARN: compliance run reported issues"
  else
    echo "   skipped (set ROUTER_API_KEY to enable)"
  fi
fi

echo "==> done"
