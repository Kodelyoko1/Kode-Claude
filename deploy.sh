#!/bin/bash
# Redeploy the website to Netlify production.
# Usage:  ./deploy.sh  [optional commit message]
set -e

MSG="${1:-Manual deploy}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBSITE_DIR="$SCRIPT_DIR/website"

export PATH="/home/tylumiere25/.hermes/node/bin:$PATH"

if ! command -v netlify > /dev/null 2>&1; then
  echo "✗ netlify CLI not found. Run: npm install -g netlify-cli"
  exit 1
fi

if [ ! -d "$WEBSITE_DIR" ]; then
  echo "✗ Website directory not found at $WEBSITE_DIR"
  exit 1
fi

echo "→ Deploying $WEBSITE_DIR to Netlify (production)..."
cd "$WEBSITE_DIR"
netlify deploy --dir=. --prod --message "$MSG"

echo ""
echo "✓ Live at https://wholesaleomniverse.netlify.app"
