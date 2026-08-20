# Testing Checklist — banked from build sessions

Work through in a dedicated testing session. Strike items as verified.
All items assume a fresh server start (`CAPACTIVE_DEV_MODE=1
venv/Scripts/python run.py`) and a hard refresh (Ctrl+Shift+R) for CSS.

## UI (2026-08-19 session)

- [ ] **Universal progress widget**: start a Full compendium build on
  Deliverables, immediately navigate to Dashboard/Portfolio — floating pill
  appears within ~3s, flips to "Finished: <file>" with a View deliverables
  link when done. Also confirm an ingest/analysis job shows in the pill on
  module pages (they never had the banner).
- [ ] **Navigation guard**: start a deliverable build, try clicking a nav
  link mid-build — browser "Leave site?" prompt appears. Cancel → build
  finishes + auto-download fires. Also confirm the download itself does NOT
  trigger the prompt.
- [ ] **Documents page layout**: type pills one-line (long types like
  "operating statement" ellipsize, full name on hover); View + PDF buttons
  stay inside the card at normal and narrow window widths.
- [ ] **Badge colors**: all document types on Documents/elsewhere render
  colored pills (no colorless ones). Flag any two types that commonly
  co-occur and read too similar (loan vs receivables are both green-family).
- [ ] **Collapsible Extraction nav**: collapsed by default; auto-opens on
  extraction pages; chevron animates; state persists across pages/reloads.
- [ ] **Tagline**: "See the signal. Make the move." under the wordmark on
  sidebar, login, setup — check it doesn't wrap awkwardly in the sidebar
  (if it does: font-size down a point or tighten letter-spacing).

## Admin / seats (Phase 1)

- [ ] **License page**: Admin → License shows "Extraction Seats x/y" and
  "Access Seats x/y" lines + per-plan rows in the comparison table.
- [ ] **Add-user form**: five roles with seat-class labels.
- [ ] **Seat enforcement**: on a non-enterprise test org, add users past a
  class limit → clear flash error; promote a viewer to extractor with
  extraction seats full → blocked at Admin → Permissions.
- [ ] **Migration**: watch server console on first start after pulling —
  `finalized_at` column added + backfill of previously-approved docs, no
  migration errors.
- [ ] **Finalize flow**: approve a doc in Review Queue → finalized_at set
  (visible via sqlite or future sync status); skip/reset clears it.

## Device auth (Phase 2a)

- [ ] **Register device**: Admin → Devices → register → token shown once
  with copy button; page shows seat usage; register disabled at limit.
- [ ] **Ping loop**:
  `curl -s -H "Authorization: Bearer <token>" -H "X-Device-Fingerprint: my-laptop" http://127.0.0.1:5000/api/sync/ping`
  — run twice (fingerprint pins then matches), then once with a different
  fingerprint → 401. No header at all → 401.
- [ ] **Revoke**: revoke the device → same curl → 401; seat freed (can
  register a replacement).

## Sync loop (Phase 2b/2c)

Full loopback test against the local server (this machine is both the
extraction device and the instance — valid end-to-end exercise):

- [ ] Register a device in Admin → Devices; create `capactive_sync.ini`
  in the repo root:
  ```
  [sync]
  url = http://127.0.0.1:5000
  token = cap_...
  ```
- [ ] `venv/Scripts/python sync_client.py --status` — connects, reports
  finalized count and delta.
- [ ] Approve at least one doc in Review Queue (creates finalized delta),
  then `sync_client.py --push` — batches push, PDFs upload with sha256
  verification.
- [ ] Re-run `--push` immediately: delta should be 0 (manifest diff works).
- [ ] Approve/correct the same doc again (re-finalize) and push: instance
  should report action=updated; check `sync_versions` table has a snapshot
  (`sqlite3 data/org_dev.db "SELECT document_id, version_no FROM sync_versions"`).
- [ ] Synced doc's PDF opens from the Documents page (filepath points at
  `data/synced_pdfs/<org>/<sha>.pdf`).
- [ ] Corrupt-transfer path: POST /api/sync/pdf with a wrong sha256 → 400.
- [ ] `sync_client.py --push --no-pdfs` works (structured only).
- [ ] **Provenance panel**: open a synced document's detail page — "Synced
  document" card shows origin device + last sync; after a re-push, the
  Version history button reveals field-level before/after diffs and term
  count changes.
