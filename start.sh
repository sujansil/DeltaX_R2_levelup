#!/usr/bin/env bash

set -e

cd "$(dirname "$0")"

echo "======================================"
echo "       DELTA X / LEVEL UP START"
echo "======================================"

FRESH=false

if [ "$1" = "--fresh" ]; then
    FRESH=true
fi


# --------------------------------------------------
# FRESH START
# --------------------------------------------------

if [ "$FRESH" = true ]; then

    echo
    echo "WARNING: FRESH MODE"
    echo "This will DELETE the local Docker database volume."
    echo

    read -p "Type RESET to continue: " CONFIRM

    if [ "$CONFIRM" != "RESET" ]; then
        echo "Cancelled."
        exit 1
    fi

    echo
    echo "[1/6] Removing old containers + volumes..."
    docker compose down -v

    echo
    echo "[2/6] Building API..."
    docker compose build api

    echo
    echo "[3/6] Starting PostgreSQL and Redis..."
    docker compose up -d db redis

    echo
    echo "[4/6] Running database migrations..."
    docker compose run --rm api alembic upgrade head

    echo
    echo "[5/6] Seeding database..."
    docker compose run --rm api python -m scripts.seed

    echo
    echo "[6/6] Starting API..."
    docker compose up -d api


# --------------------------------------------------
# NORMAL START
# --------------------------------------------------

else

    echo
    echo "NORMAL START"
    echo "Existing database will be preserved."
    echo

    echo "[1/5] Building API..."
    docker compose build api

    echo
    echo "[2/5] Starting PostgreSQL and Redis..."
    docker compose up -d db redis

    echo
    echo "[3/5] Running any pending migrations..."
    docker compose run --rm api alembic upgrade head

    echo
    echo "[4/5] Starting API..."
    docker compose up -d api

    echo
    echo "[5/5] Checking containers..."
fi


echo
docker compose ps

echo
echo "======================================"
echo "              READY"
echo "======================================"
echo
echo "Login:"
echo "  http://localhost:8000"
echo
echo "Audience:"
echo "  http://localhost:8000/audience"
echo
echo "Useful commands:"
echo "  docker compose logs -f api"
echo "  docker compose ps"
echo "  docker compose down"
echo
