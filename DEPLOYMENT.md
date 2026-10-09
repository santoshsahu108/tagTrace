# Tag Trace — deployment plan (bare-metal server)

Target layout on one Linux server (Ubuntu/Debian assumed):

```
Internet ──HTTPS──> Caddy (:443, auto Let's Encrypt)
                     ├── /        → web UI static files (/var/www/tagtrace)
                     └── /api/*   → collector on 127.0.0.1:8020
collector (systemd: tagtrace) ──TLS──> Neon Postgres (tagTrace / production)
                              ──────> Google Find Hub (tokens in /etc/tagtrace/secrets.json)
Android app ──HTTPS──> https://<domain>/api
```

## Secrets — where each one lives

| Secret | Local (Mac) | Server | In git? |
|---|---|---|---|
| Neon `DATABASE_URL` | `GoogleFindMyTools/.env` | `/etc/tagtrace/tagtrace.env` | never |
| Google tokens | `GoogleFindMyTools/Auth/secrets.json` | `/etc/tagtrace/secrets.json` (`GFMT_SECRETS_FILE`) | never |
| App login users | Neon DB (`users` table, PBKDF2 hashes) | same | never |
| Location history | Neon DB / old `locations.json` | Neon DB | never |
| Android signing key | `android/key.properties`, `*.jks` | not needed | never |

Templates that *are* committed: `GoogleFindMyTools/.env.example`,
`GoogleFindMyTools/deploy/tagtrace.env.example`, `web/.env.example`.

## Phase 0 — Repo (on the Mac)

1. Create a **public** GitHub repo. GoogleFindMyTools is GPL-3.0, so the
   combined project is published under GPL-3.0 too (LICENSE at the root).
2. Point git at it and push (see "Repo layout" below for the one decision).
3. Before the first push, check nothing secret is staged:
   ```
   git status --short --ignored | grep -E 'secrets.json|\.env$|\.neon|locations.json'   # must show !! (ignored)
   git diff --cached --name-only | grep -E 'secrets|\.env$|\.neon|locations' && echo STOP
   ```

## Phase 1 — Server base

```
sudo apt update && sudo apt install -y python3 python3-venv git caddy ufw
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
sudo useradd --system --home /opt/tagtrace --shell /usr/sbin/nologin tagtrace
sudo mkdir -p /opt/tagtrace /etc/tagtrace /var/www/tagtrace
sudo chown tagtrace:tagtrace /opt/tagtrace /etc/tagtrace && sudo chmod 700 /etc/tagtrace
```

Point a domain's DNS **A record** at the server's public IP (needed for HTTPS).

## Phase 2 — Collector

```
sudo -u tagtrace git clone <your-repo-url> /opt/tagtrace/src        # deploy key / token
sudo ln -s /opt/tagtrace/src/GoogleFindMyTools /opt/tagtrace/GoogleFindMyTools
cd /opt/tagtrace/GoogleFindMyTools
sudo -u tagtrace python3 -m venv .venv
sudo -u tagtrace .venv/bin/pip install -r requirements.txt -r requirements-collector.txt
```

Secrets (copied from the Mac over SSH, never via git):

```
# on the Mac
scp GoogleFindMyTools/Auth/secrets.json server:/tmp/secrets.json
# on the server
sudo install -m 600 -o tagtrace -g tagtrace /tmp/secrets.json /etc/tagtrace/secrets.json && rm /tmp/secrets.json
sudo install -m 600 -o tagtrace -g tagtrace deploy/tagtrace.env.example /etc/tagtrace/tagtrace.env
sudo -e /etc/tagtrace/tagtrace.env      # paste DATABASE_URL, set TAG_NAME
```

The Google login needs Chrome, so do it on the Mac (already done) and copy
`secrets.json`; the server never needs a browser. If tokens ever expire,
re-run the collector on the Mac once and copy the file again.

Create your login on the Neon DB, then start the service:

```
sudo -u tagtrace env $(sudo cat /etc/tagtrace/tagtrace.env | xargs) \
  .venv/bin/python collector.py --add-user you@example.com --password '<strong password>'
sudo cp deploy/tagtrace.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tagtrace
journalctl -u tagtrace -f        # expect "[db] connected" and "Tracking ..."
```

Stop the collector on the Mac once the server is polling, so two pollers
don't run against the same Google account.

## Phase 3 — Web UI + HTTPS

```
# build (on the Mac or the server; needs Node)
cd web && echo 'VITE_API_URL=/api' > .env && npm ci && npm run build
rsync -a dist/ server:/var/www/tagtrace/          # or copy on the server
# Caddy
sudo cp GoogleFindMyTools/deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/tagtrace.example.com/<your domain>/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl https://<your domain>/api/health            # {"ok": true, "total": N}
```

## Phase 4 — Android app

In the app's Settings set the collector URL to `https://<your domain>/api`
and log in with the user from Phase 2. Rebuild the APK only if you want to
change the default hint.

## Phase 5 — Operate

- **Update:** `cd /opt/tagtrace/src && sudo -u tagtrace git pull && sudo systemctl restart tagtrace`; rebuild/rsync `web/dist` when the UI changes.
- **Logs:** `journalctl -u tagtrace --since today`.
- **Backups:** Neon keeps history and branches; optionally a nightly `pg_dump` from cron.
- **Secrets rotation:** reset the Neon role password in the console, update `/etc/tagtrace/tagtrace.env`, restart. Revoke unused Neon API keys with `neon api-keys list` / `neon api-keys revoke <id>`.
- **Copy old history:** see `GoogleFindMyTools/CLOUD_DATABASE.md` step 5.

## Repo layout (decide before first push)

`~/Documents/task3` is not its own git repo today (git sees it as part of
your home folder), and `GoogleFindMyTools/` is a clone of the upstream repo.

- **Option A — one repo (recommended):** `git init` in `task3`, move
  `GoogleFindMyTools/.git` aside so its files are committed as part of
  Tag Trace, push everything to one public repo. One clone on the server.
- **Option B — two repos:** fork GoogleFindMyTools, push the
  collector there; push `task3` (app + web) separately and add the fork as a
  submodule.
