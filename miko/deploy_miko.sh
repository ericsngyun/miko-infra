#!/bin/bash
# deploy_miko.sh
# Run from your laptop or directly on the node after cloning
# Usage: bash deploy_miko.sh

set -e

MIKO_DIR="$HOME/awaas/miko"

echo "→ Creating Miko directory structure"
mkdir -p "$MIKO_DIR/static"

echo "→ Copying files"
cp miko_bot.py       "$MIKO_DIR/"
cp SOUL.md           "$MIKO_DIR/"
cp requirements_miko.txt "$MIKO_DIR/requirements.txt"
cp Dockerfile_miko   "$MIKO_DIR/Dockerfile"
cp miko_ui.html      "$MIKO_DIR/static/index.html"
cp miko_docker_compose.yml "$MIKO_DIR/docker-compose.yml"

echo "→ Writing .env"
cat > "$MIKO_DIR/.env" << EOF
TELEGRAM_BOT_TOKEN=8279483268:AAFa05Z-p3z3YAumR7iV3kAhxKBuzXIvEiY
ERIC_CHAT_ID=7355900090
DAVID_CHAT_ID=1697120532
EOF

echo "→ Building and starting Miko"
cd "$MIKO_DIR"
docker compose up -d --build

echo "→ Waiting for health check"
sleep 8
curl -s http://localhost:8400/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('✓ Miko online:', d)"

echo ""
echo "Done. Miko is running at:"
echo "  Web UI:  http://100.73.88.88:8400"
echo "  Health:  http://100.73.88.88:8400/health"
echo "  API:     http://100.73.88.88:8400/api/chat"
echo ""
echo "Telegram bot is live on your existing bot token."
