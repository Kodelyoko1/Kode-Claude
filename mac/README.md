# iMessage Relay — Mac Bridge

Turns "deal closed" pings from the Linux server into real iMessages
(blue bubbles) on your phone, by relaying them through an always-on Mac
that has Messages.app signed into your iCloud.

## Files

- `imessage_send.applescript` — one-shot sender; called by the listener
- `imessage_listener.py` — HTTP server on the Mac; receives `POST /send` from
  the Linux box and shells to AppleScript

## One-time setup

### 1. Copy this `mac/` directory to the Mac

```bash
# from this Linux box
scp -r mac/ you@your-mac:~/wholesale-omniverse-mac/
```

### 2. Open Messages.app on the Mac and sign into iCloud

Verify you can manually send an iMessage to `tylumiere25@icloud.com`
(or whatever your destination handle is). The relay won't work if the
target isn't reachable via iMessage.

### 3. Give Terminal / Python "Automation" permission

System Settings → Privacy & Security → Automation → allow Terminal (or
Python) to control Messages. macOS will prompt on first run.

### 4. Pick a shared secret

```bash
# on BOTH the Mac and the Linux box
export IMESSAGE_SECRET="$(openssl rand -hex 24)"
```

### 5. Start the listener on the Mac

```bash
# foreground (for testing)
IMESSAGE_SECRET="..." python3 imessage_listener.py

# background (for production)
nohup IMESSAGE_SECRET="..." python3 imessage_listener.py > relay.log 2>&1 &
```

To run as a LaunchAgent (auto-start on login, restart on crash), drop a
`~/Library/LaunchAgents/com.wholesale.imessage-relay.plist` with the
script path + EnvironmentVariables and `launchctl load` it.

### 6. Make the Mac reachable from this Linux box

**Easiest: Tailscale (recommended, free)**

```bash
# on Mac
brew install --cask tailscale && open /Applications/Tailscale.app
# on Linux
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
```

After both are connected, the Mac is at `http://<mac-hostname>:8787`
inside the Tailnet — no NAT, no port-forwarding, no public exposure.

**Alternative: ngrok (no install on the Linux side, good for testing)**

```bash
# on Mac
ngrok http 8787
# copy the https://....ngrok.app URL into IMESSAGE_RELAY_URL on the Linux side
```

### 7. Tell the Linux side where the Mac is

Add to `.env` on the Linux box:

```
IMESSAGE_RELAY_URL=http://your-mac.tail-scale-net.ts.net:8787
IMESSAGE_SECRET=<same long random string as on the Mac>
IMESSAGE_TO=tylumiere25@icloud.com
```

### 8. Test from the Linux box

```bash
python3 -c "from autonomous.notify import send_sms; \
  print(send_sms('test ping from wholesale-omniverse'))"
```

You should see a notification on your iPhone within ~2 seconds, and the
return value should be `{'channel': 'imessage', 'status': 'sent', ...}`.

## Sleep prevention on the Mac

Messages.app must be running and the Mac must be awake. Options:

- System Settings → Battery → Options → "Prevent automatic sleeping…"
- Or run `caffeinate -di &` from a terminal that stays open

## Failure mode

If the relay is unreachable, the notification falls through automatically:
**iMessage → Twilio (if configured) → email-to-SMS gateway (if configured) →
email to OWNER_EMAIL**. You never miss a deal-close ping.
