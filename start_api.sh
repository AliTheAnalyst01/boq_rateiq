#!/bin/bash
# Start RateIQ API server

cd ~/Development/boq_rateiq

echo "Starting Docker services..."
docker compose -f docker/docker-compose.yml up -d

echo "Waiting for Qdrant + PostgreSQL..."
sleep 3

echo "Starting RateIQ API on http://localhost:8000"
echo "Docs available at http://localhost:8000/docs"
echo ""

uv run uvicorn src.rateiq.api:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --log-level info