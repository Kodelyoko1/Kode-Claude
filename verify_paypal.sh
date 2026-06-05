#!/usr/bin/env bash
# Verify PayPal CLIENT_ID + SECRET work end-to-end with a live token call.
# Run this AFTER pasting your credentials into .env.

set -u
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "✗ .env not found"
    exit 1
fi

export $(grep -v '^#' .env | xargs) 2>/dev/null

echo "PayPal credential check"
echo ""

# 1. Are vars filled?
if [ -z "${PAYPAL_CLIENT_ID:-}" ] || [ -z "${PAYPAL_CLIENT_SECRET:-}" ]; then
    echo "  ✗ PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is empty in .env"
    echo ""
    echo "  Fix:"
    echo "    1. Visit https://developer.paypal.com/dashboard/applications/live"
    echo "    2. Open your live app"
    echo "    3. Copy CLIENT_ID and CLIENT_SECRET into .env (lines 34-35)"
    echo "    4. Re-run this script"
    exit 1
fi

echo "  ✓ PAYPAL_CLIENT_ID set (${#PAYPAL_CLIENT_ID} chars)"
echo "  ✓ PAYPAL_CLIENT_SECRET set (${#PAYPAL_CLIENT_SECRET} chars)"
echo "  ✓ PAYPAL_MODE=${PAYPAL_MODE:-unset}"

# 2. Live OAuth token call
echo ""
echo "  Calling PayPal OAuth..."
python3 - <<'PYEOF'
import os, sys
sys.path.insert(0, ".")
try:
    from paywall.paypal import _get_token
    t = _get_token()
    if t and len(t) > 20:
        print(f"  ✓ OAuth token obtained ({len(t)} chars)")
        print(f"  ✓ PayPal is ready — agents can now create invoices and collect payments.")
        sys.exit(0)
    print(f"  ✗ Token call returned: {t!r}")
    sys.exit(1)
except Exception as e:
    print(f"  ✗ {type(e).__name__}: {str(e)[:200]}")
    sys.exit(1)
PYEOF
