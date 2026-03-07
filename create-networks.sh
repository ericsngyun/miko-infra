#!/bin/bash
# create-networks.sh — Run ONCE before deploying any stack
# Creates all Docker networks that compose files reference as external
set -euo pipefail

echo "Creating AWaaS Docker networks..."

for net in awaas-shared awaas-monitor awaas-orchestrator awaas-pleadly awaas-services awaas-trading; do
  docker network inspect "$net" >/dev/null 2>&1 || {
    docker network create "$net"
    echo "  ✓ $net"
  }
done

echo "All networks ready."
