#!/bin/bash
# ============================================================================
# AWaaS Per-Stack Secrets Generator
# Run ONCE on your laptop. Copy output into SOPS-encrypted yaml files.
# ============================================================================
set -euo pipefail

gen() { openssl rand -base64 32 | tr -d '=/+' | head -c "$1"; }
gen_hex() { openssl rand -hex "$1"; }

echo "# ============================================================"
echo "# AWaaS Per-Stack Secrets — Generated $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "# Copy these into your SOPS yaml files, then encrypt."
echo "# NEVER commit this output unencrypted."
echo "# ============================================================"
echo ""

echo "# --- secrets/shared/prod.yaml (append to existing) ---"
echo "redis_password: $(gen 32)"
echo "caddy_admin_password: $(gen 24)"
echo ""

echo "# --- secrets/orchestrator/prod.yaml ---"
echo "master_postgres_password: $(gen 32)"
echo "conductor_api_key: $(gen 32)"
echo "miko_api_key: $(gen 32)"
echo "action_gateway_hmac_key: $(gen_hex 32)"
echo ""

echo "# --- secrets/pleadly/prod.yaml ---"
echo "pleadly_postgres_password: $(gen 32)"
echo "pleadly_qdrant_api_key: $(gen 32)"
echo "pleadly_api_secret: $(gen 32)"
echo "pleadly_hmac_key: $(gen_hex 32)"
echo ""

echo "# --- secrets/awaas_services/prod.yaml ---"
echo "awaas_postgres_password: $(gen 32)"
echo "awaas_qdrant_api_key: $(gen 32)"
echo "awaas_api_secret: $(gen 32)"
echo ""

echo "# --- secrets/trading/prod.yaml ---"
echo "trading_postgres_password: $(gen 32)"
echo "trading_redis_password: $(gen 32)"
echo "trading_api_secret: $(gen 32)"
echo ""

echo "# --- secrets/monitor/prod.yaml ---"
echo "grafana_admin_password: $(gen 24)"
echo "alertmanager_webhook_secret: $(gen 32)"
echo ""

echo "# --- HMAC Keys for Inter-Service Auth ---"
echo "vercel_webhook_hmac_key: $(gen_hex 32)"
echo "llm_gateway_audit_hmac: $(gen_hex 32)"
echo ""
echo "# ============================================================"
echo "# DONE. Encrypt each namespace file with:"
echo "#   sops -e -i secrets/<stack>/prod.yaml"
echo "# ============================================================"
