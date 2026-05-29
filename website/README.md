# Wholesale Omniverse — Website

Static site for **wholesaleomniverse.com**. 4 pages, mobile responsive, no framework, no build step.

```
website/
├── index.html        Hub page
├── sell.html         Seller intake (writes leads → data/leads.json)
├── buyers.html       $97/mo Priority Buyers List subscription
├── tools.html        Deal Analyzer + OAS pricing
├── styles.css        Shared styles (navy + amber brand)
├── script.js         Form handler + mobile nav
└── assets/logo.png   Brand mark
```

## Local preview

```bash
cd website && python3 -m http.server 8080
# Open http://localhost:8080
```

## Deploy — pick one path

### Option A — Cloudflare Pages (recommended: free, fast, SSL included)

1. Push the `website/` folder to a GitHub repo (or use Cloudflare's drag-and-drop upload)
2. Sign in at **https://pages.cloudflare.com/**
3. **Create a project → Connect to Git → pick the repo**
4. Build settings:
   - Framework preset: **None**
   - Build command: *(leave blank)*
   - Build output directory: `/` (or `website` if uploading whole repo)
5. **Custom domain → Add wholesaleomniverse.com**
   - Cloudflare gives you DNS records → point them at your registrar
   - SSL auto-issues in ~10 minutes
6. Deploy lives at `wholesaleomniverse.com` in under 5 minutes

### Option B — Netlify (also free, has its own form-handler backup)

1. Drag-and-drop the `website/` folder onto **https://app.netlify.com/drop**
2. Get a `*.netlify.app` URL immediately
3. Site settings → Custom domain → add `wholesaleomniverse.com`
4. Follow Netlify's DNS instructions

### Option C — Your own server (full control + lead capture endpoint)

If you want the `/leads` endpoint live (so seller form writes directly into your `data/leads.json`), you need to host the Flask server too:

```bash
# On your server (VPS, home server, Raspberry Pi, whatever)
cd /path/to/wholesale_agent
pip install -r requirements.txt
python3 paywall_server.py    # runs Flask on port 5055
```

Then put nginx in front to serve `website/` statically and reverse-proxy `/leads` to Flask:

```nginx
server {
    listen 80;
    server_name wholesaleomniverse.com;

    root /path/to/wholesale_agent/website;
    index index.html;

    location /leads {
        proxy_pass http://127.0.0.1:5055/leads;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location / {
        try_files $uri $uri.html $uri/ =404;
    }
}
```

## What happens when someone submits the seller form?

1. JS posts JSON to `/leads`
2. `paywall_server.py` writes a new entry to `data/leads.json` with `status: new`
3. An instant email notification fires to `DIGEST_EMAIL` (or `SMTP_USER`)
4. The existing 6-touch follow-up agent (`run_followup_auto.py`) picks it up on its next run
5. If the `/leads` endpoint isn't reachable (e.g., Cloudflare-only deploy), the form gracefully falls back to opening the user's mail client with the lead info prefilled, sent to `WholesaleOmniverse@gmail.com`

## Tweaking copy/colors

- **Brand colors**: `:root` block at the top of `styles.css` — change `--amber`, `--navy`, etc.
- **Phone/email**: search-and-replace `207-385-4041` and `WholesaleOmniverse@gmail.com` across the four HTML files
- **PayPal subscribe link**: search for `paypal.me/wholesaleomniverse` and update if needed
- **Pinterest city links**: the `?city=` query param on `sell.html` auto-prefills the city field (matches the Pinterest pin URLs)
