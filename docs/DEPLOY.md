# Deploying a Capactive Org Instance

_Companion to `docs/PACKAGING_DESIGN.md`. One container image, two
operating modes: **managed** (we run it for the client) and
**client-hosted** (their VM/container host). Same image, same steps —
only who executes them differs._

## What the instance is (and isn't)

The instance receives sync pushes from licensed extraction devices and
serves dashboards, modules, and deliverables to access users. It does
**no LLM extraction** — Ollama never runs here; documents are processed
on extraction devices and arrive as structured data + source PDFs.

## Requirements

- Docker + Docker Compose on any Linux VM (2 vCPU / 4 GB / 40 GB disk is
  comfortable for a single org).
- For TLS mode: a DNS name pointing at the VM, ports 80/443 open.
- For plain mode: the client's own reverse proxy/VPN in front; the app
  binds to localhost:5000 only.

## First-time provisioning

```bash
git clone <repo> capactive && cd capactive     # or unpack a release tarball

# generate the instance secret ONCE and keep it (env file, secret store):
python3 -c "import secrets; print(secrets.token_hex(32))"

# TLS mode (managed default):
CAPACTIVE_SECRET_KEY=<key> CAPACTIVE_DOMAIN=client.capactive.app \
    docker compose --profile tls up -d --build

# plain mode (client infra behind their proxy):
CAPACTIVE_SECRET_KEY=<key> docker compose up -d --build
```

Then open the site → the **setup** flow creates the organization (plan,
license key) and first admin. In Admin → Devices, register each
extraction seat and hand its one-time token to the device operator
(configure `capactive_sync.ini` on the device **in the same sitting** —
the first contact pins the device fingerprint).

Store the secret key in an `.env` file next to the compose file if you
prefer (`CAPACTIVE_SECRET_KEY=...`); compose reads it automatically.

## Data & state

Everything persistent lives in the `capactive_data` volume: org SQLite
DBs, the central config DB, synced source PDFs, generated deliverables.
The image is stateless — rebuild/replace it freely; never bake data in
(enforced by `.dockerignore`).

## Backups

```bash
docker compose exec -T app sh deploy/backup.sh      # run one
# host cron, 03:00 nightly:
0 3 * * * cd /opt/capactive && docker compose exec -T app sh deploy/backup.sh
```

Uses `sqlite3 .backup` (safe with live writers), tars synced PDFs and
deliverables, keeps 14 days (`CAPACTIVE_BACKUP_RETAIN_DAYS`). Copy
`data/backups/` off-host (rclone/S3/whatever the client trusts) for real
disaster recovery. Restore = stop app, copy the .db files back into the
volume, start app.

## Updates

```bash
git pull                                # or unpack the new release
docker compose --profile tls up -d --build
```

DB migrations run automatically on first touch (the `_migrate` pattern).
Take a backup first out of habit.

## Environment reference

| Variable | Required | Purpose |
|---|---|---|
| `CAPACTIVE_SECRET_KEY` | **yes** | session signing; stable across restarts |
| `CAPACTIVE_DOMAIN` | tls profile | Caddy hostname for auto-HTTPS |
| `CAPACTIVE_DATA_DIR` | set by image | persistent state root |
| `CAPACTIVE_CONFIG_DB` | set by image | central config DB path |
| `CAPACTIVE_BACKUP_RETAIN_DAYS` | no (14) | backup retention |
| `CAPACTIVE_RATE_LIMIT` | no (30) | uploads/min per org |

Never set `CAPACTIVE_DEV_MODE=1` on an instance — it bypasses login.

## Security posture (client conversations)

- Extraction and LLM processing happen on the client's own devices; the
  instance holds structured results + source PDFs for their org only.
- Single-tenant by architecture: one instance, one org, one volume.
- TLS via Caddy/Let's Encrypt; HSTS + standard hardening headers.
- Device access: Bearer tokens stored hashed, fingerprint-pinned,
  revocable in Admin → Devices. User access: role templates + seat
  licensing. All access auditable (`usage.py` trail).

## Smoke test after provisioning

1. `docker compose ps` — app healthy (healthcheck hits `/api/status`).
2. Browser: setup flow → create org + admin → log in.
3. Admin → Devices → register a device; from the extraction machine:
   `python sync_client.py --status` connects.
4. Push a finalized document; confirm it appears with its "Synced
   document" provenance card and the PDF opens.
