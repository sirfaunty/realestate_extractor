# Docker Validation Session — Runbook

_Goal: prove the container story end to end on a real host, as a dress
rehearsal for provisioning client #1. Companion: `docs/DEPLOY.md` (the
runbook being validated) and the Phase 3 section of
`TESTING_CHECKLIST.md`._

## Before the session (~15 min, do anytime)

- [ ] Spin up a throwaway VM: Ubuntu 24.04, 2 vCPU / 4 GB / 40 GB
  (Hetzner ~€5/mo or DigitalOcean ~$6/mo). Note the IP.
- [ ] Firewall: allow inbound 22, 80, 443.
- [ ] Optional but recommended (enables the TLS test): point a DNS
  A record you control at the VM (e.g. `test.yourdomain.com`) — do this
  early so it propagates.
- [ ] SSH in, install Docker:
  `curl -fsSL https://get.docker.com | sh` (includes compose plugin).
- [ ] Push latest to GitHub from the ASUS so the VM clones current code.

## Session flow (in order)

### 1. Provision (plain mode)
```bash
git clone <repo-ssh-url> capactive && cd capactive
python3 -c "import secrets; print(secrets.token_hex(32))"   # save this
echo "CAPACTIVE_SECRET_KEY=<key>" > .env
docker compose up -d --build          # first build takes a few minutes
docker compose ps                     # expect: healthy
```
- [ ] Negative check first: `docker compose up` WITHOUT the .env key —
  expect refusal naming CAPACTIVE_SECRET_KEY.
- [ ] `ssh -L 5000:127.0.0.1:5000 <vm>` from the ASUS, then browse
  http://localhost:5000 → setup flow → create org + admin → log in.

### 2. TLS profile
```bash
echo "CAPACTIVE_DOMAIN=test.yourdomain.com" >> .env
docker compose --profile tls up -d
```
- [ ] https://test.yourdomain.com loads with a valid Let's Encrypt cert;
  http:// redirects to https.

### 3. Real-world sync (the headline test)
On the instance (browser): Admin → Devices → register `asus-extractor-2`,
copy token. On the ASUS:
```bash
# point the client at the VM (new ini or --url/--token flags):
venv/Scripts/python sync_client.py --url https://test.yourdomain.com --token cap_... --status
venv/Scripts/python sync_client.py --url https://test.yourdomain.com --token cap_... --push --limit 5
```
- [ ] Push succeeds over the public internet; docs appear on the
  instance with provenance cards.
- [ ] PDF upload works through Caddy (512 MB body limit is configured —
  verify a real PDF lands and opens from the instance's Documents page;
  ingest one fresh doc locally first if needed so a filepath resolves).
- [ ] Wrong fingerprint / revoked token → 401 through the proxy too.

### 4. Operator console on the instance
```bash
docker compose exec app python create_operator.py you@capactive.com "Patrick"
```
- [ ] /operator/login → console lists the org; enter workspace → banner;
  exit; audit shows it.

### 5. Operations drills
- [ ] Backup: `docker compose exec -T app sh deploy/backup.sh` →
  verify files in the volume (`docker compose exec app ls data/backups`).
- [ ] Restart: `docker compose restart app` → still logged in
  (secret stable), data intact.
- [ ] Update: make a trivial commit, `git pull && docker compose
  --profile tls up -d --build` → new code live, data intact.
- [ ] Restore drill once: copy a backup .db over the live one (app
  stopped), restart, confirm state.

### 6. Wrap
- [ ] Record timings + any deviations in TESTING_CHECKLIST.md (fixes go
  to DEPLOY.md — the runbook must match reality by session end).
- [ ] Tear down the VM or keep it as the staging instance.

## Likely snags (so they don't burn time)

- Cert fails → DNS not propagated or port 80 blocked; check
  `docker compose logs caddy`.
- Build fails on a dependency → note it; opencv/pymupdf wheels
  occasionally lag new Python — pin in requirements if so.
- Setup page won't load → check `docker compose logs app` for an
  import error; the image runs code paths Windows never exercised.
- Sync 413 (body too large) → raise Caddy request_body max_size.
