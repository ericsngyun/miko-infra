#!/bin/bash
# create-data-dirs.sh — Run ONCE before deploying any stack
# Creates all bind mount directories on /mnt/data (lv_data volume)
set -euo pipefail

echo "Creating data directories on /mnt/data..."

dirs=(
  master-postgres
  pleadly-postgres
  pleadly-qdrant
  pleadly-n8n
  awaas-postgres
  awaas-qdrant
  awaas-n8n
  trading-postgres
  trading-redis
  redis
  prometheus
  grafana
  loki
)

for d in "${dirs[@]}"; do
  mkdir -p "/mnt/data/$d"
  echo "  ✓ /mnt/data/$d"
done

# Set ownership for services that need specific UIDs
chown 999:999 /mnt/data/redis           # Redis
chown 999:999 /mnt/data/trading-redis    # Trading Redis
chown 472:472 /mnt/data/grafana          # Grafana
chown 10001:10001 /mnt/data/loki         # Loki
chown 65534:65534 /mnt/data/prometheus    # Prometheus (nobody)
chown 1000:1000 /mnt/data/pleadly-qdrant # Qdrant
chown 1000:1000 /mnt/data/awaas-qdrant   # Qdrant
chown 1000:1000 /mnt/data/pleadly-n8n    # n8n
chown 1000:1000 /mnt/data/awaas-n8n      # n8n

echo "Data directories created and ownership set."
