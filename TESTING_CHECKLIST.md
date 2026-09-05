# Testing Checklist — banked from build sessions

Work through in a dedicated testing session. Strike items as verified.
All items assume a fresh server start (`CAPACTIVE_DEV_MODE=1
venv/Scripts/python run.py`) and a hard refresh (Ctrl+Shift+R) for CSS.

## UI (2026-08-19 session)

- [~] **Universal progress widget**: PARTIAL 2026-08-19 — deliverable job
  registration bug found + fixed (package-relative import); builds on
  current data finish in ~2s so they slip between 3s polls. Mechanism
  verified in code; LIVE-VERIFY on first long job (ingest/analysis or a
  client-size deliverable build).
- [x] **Navigation guard**: PASS 2026-08-19 — dialog fires via _guard
  listener test; auto-download fires post-build without triggering it.
  (Guard-during-real-build untestable at 2s build times — same listener,
  low risk.)
- [x] **Documents page layout**: PASS 2026-08-19 — pills one-line, hover
  tooltip works (native delay), buttons contained at desktop width.
- [x] **Badge colors**: PASS 2026-08-19 — all colored, no confusing pairs
  spotted in current data.
- [x] **Collapsible Extraction nav**: PASS 2026-08-19 — toggle/chevron
  good; sticky manual preference confirmed working as designed
  (localStorage cleared → default collapsed).
- [x] **Tagline**: PASS 2026-08-19 at desktop width.
- [ ] Low priority: check tagline + nav rendering at mobile breakpoints
  (≤1124px / ≤480px) before any tablet/phone usage matters.

## Admin / seats (Phase 1)

- [x] **License page**: PASS 2026-08-19 — both seat lines + plan table rows
  render.
- [ ] **Add-user form**: five roles with seat-class labels.
- [ ] **Seat enforcement**: on a non-enterprise test org, add users past a
  class limit → clear flash error; promote a viewer to extractor with
  extraction seats full → blocked at Admin → Permissions.
- [x] **Migration**: PASS 2026-08-19 — /documents 200, no traceback on
  first org-DB touch after relaunch.
- [ ] **Finalize flow**: approve a doc in Review Queue → finalized_at set
  (visible via sqlite or future sync status); skip/reset clears it.

## Device auth (Phase 2a)

- [x] **Register device**: PASS 2026-08-19 — one-time token + copy button,
  five-role dropdown confirmed on Users page. (Note: pin happens during
  auth, so fingerprint_pinned=true on first ping — correct.)
- [x] **Ping loop**: PASS 2026-08-19 — valid+pinned OK; wrong fingerprint
  → 401 rejected.
- [x] **Revoke**: PASS 2026-08-19 — revoked token 401s even with correct
  fingerprint; seat freed for re-registration.
- [ ] To-do (build): admin "reset fingerprint" action on Devices page —
  today a rebuilt machine requires revoke + re-register. Also note: TOFU
  means whoever contacts first pins the print; issue tokens and configure
  the client in one sitting.

## Sync loop (Phase 2b/2c)

Full loopback test against the local server (this machine is both the
extraction device and the instance — valid end-to-end exercise):

- [x] **Full sync loop**: PASS 2026-08-20 — 140 docs pushed in 6 batches,
  0 failed; idempotent receive (crashed batch's docs recognized on retry);
  loop-guard verified (synced copies excluded from source count); versioned
  re-push (doc 41 correction → v1 snapshot).
- [x] **PDF endpoint**: PASS 2026-08-20 — upload+attach 200, sha mismatch
  400, unknown origin_doc_id 404, content-hash dedupe confirmed.
- [x] **Provenance panel**: PASS 2026-08-20 (browser-verified) — synced
  card w/ origin device + version count; history shows field-level diff.

Bugs found + fixed during this run (all committed):
- deliverables job registration: bare `import webapp` failed under package
  execution → package-relative import.
- api_sync_run: `logger` undefined in webapp.py → `app.logger`.
- relay-loop risk: synced copies re-pushed forever in loopback → sync
  sources exclude origin rows.
- sync_client: silent on non-200 and connection refused → explicit errors;
  added `--limit N`.

## Phase 3 — container (needs a Docker host; ideally a throwaway Linux VM)

- [x] `docker compose up -d --build`: PASS 2026-09-02 on Hetzner CX23 —
  63s build, 4.3 MB context, waitress serving, container healthy, no
  import errors on Linux. (Setup-flow check pending — Step 4.)
- [x] Compose refuses without CAPACTIVE_SECRET_KEY: PASS 2026-09-02.
  Side-find: CAPACTIVE_DOMAIN was also demanded in plain mode → defaulted
  to localhost in compose.
- [x] Full smoke test: PASS 2026-09-04 — setup flow → staging-org + admin
  → device registered → pushes from ASUS over SSH tunnel: new doc, PDF
  served from instance, property find-or-create ("Test Property"),
  versioned update ("1 prior version" + diff). Ingest also works directly
  on the Linux container (accidental test, no Ollama).
  **Bug found+fixed remotely:** device-local FK ids (property/building/
  unit/portfolio) violated instance FKs → dropped + property resolved by
  name. Protocol extended to carry `clauses`.
- [ ] TLS profile — DEFERRED: no DNS record yet. Add A record → VM, then
  `echo CAPACTIVE_DOMAIN=... >> .env && docker compose --profile tls up -d`.
- [x] Backup drill: PASS 2026-09-04 — config/org/registry DBs + synced_pdfs
  archive in timestamped folder. (Restore drill still to do once.)
- [x] Restart: PASS 2026-09-04 — `docker compose restart app`, session
  survived, data intact. Update drill also PASS (git pull + rebuild,
  cached layers, <1s, volume untouched).
- [x] Operator console on instance: PASS 2026-09-04 — create_operator.py
  inside container, login, org listed, enter workspace (banner) / exit,
  audit shows login + entered + exited.

VM: Hetzner CX23 Nuremberg 91.98.30.65, ~$7/mo — KEEP as the standing
staging instance (demo target, future TLS test, client rehearsals).

## Operator console (super admin — built 2026-08-20)

- [ ] Bootstrap: `venv/Scripts/python create_operator.py you@capactive.com "Patrick"`
  (prompts for 12+ char password) → then log in at `/operator/login`.
- [ ] Console lists all orgs (dev + any test orgs) with plan, status,
  users, devices/seats, license key.
- [ ] Provision a test org from the console → flash shows license key;
  new org's admin can log in normally at /login.
- [ ] Set plan + deactivate/reactivate the test org — audit table at the
  bottom records each action.
- [ ] **Enter workspace** on the test org → lands on its dashboard with
  the orange operator banner pinned on top; act as admin (e.g. open
  Admin → License); **Exit to console** returns to /operator; both entry
  and exit appear in the audit log.
- [ ] Operator login required even in dev mode (no DEV_MODE bypass);
  /operator redirects to login when signed out.

Findings from the Docker session (2026-09-03/04):
- [x] **Confirm-modal bug (FIXED)**: `capModal.confirm` resolved false on
  every confirm click (`_close()` settled the promise first) — Re-extract
  and Admin→Users deactivate had never actually fired from the UI.
- [ ] **Re-extract changes document identity**: it deletes + re-creates
  the row (605→606→608), resetting review status and breaking sync
  continuity (instance sees a new doc, not a new version). Should preserve
  id, or sync should key on content hash. Design item before client use.
- [ ] Sample-doc analysis never produced terms/clauses via upload or
  per-doc Re-extract (`analysis_status: ingested`); the lease pipeline
  runs from the property-level Analyze. Clarify the intended operator
  path in the setup guide, or make upload trigger analysis.

Open findings for later:
- [~] **Filepath hygiene**: root cause found — DB migrated from the Mac
  with frozen absolute paths; ALL 424 files still exist under local
  uploads/. `relink_documents.py` built + rehearsed on a DB copy
  (424/424 resolved, prefix swap). TO RUN natively:
  `venv/Scripts/python relink_documents.py --apply`, then spot-check
  View PDF. Tool is a keeper — same situation will occur at client
  machine migrations.
- [ ] Devices page: "reset fingerprint" action (avoid revoke+re-register
  on machine rebuild).
- [ ] synced_pdfs orphan sweep (404'd uploads leave unreferenced hashes).
- [ ] Provenance card: format synced_at without the ISO "T"; consider
  dropping finalized_at from diff rows (redundant with replaced_at).
- [ ] Dev-DB cleanup (optional): 140 synced mirror copies now sit alongside
  originals on the Documents page. To purge:
  `DELETE FROM financial_terms WHERE document_id IN (SELECT id FROM documents WHERE origin_device_id IS NOT NULL);`
  `DELETE FROM sync_versions; DELETE FROM documents WHERE origin_device_id IS NOT NULL;`
