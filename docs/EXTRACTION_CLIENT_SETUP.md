# Extraction Client — Setup Guide (Windows)

_The extraction client is Capactive running on a licensed desk: it
ingests documents, runs OCR and the local AI model, and pushes finalized
results (plus source PDFs) to the organization's web instance. Nothing
about a document ever leaves this machine except what the operator
finalizes. Companion: `docs/DEPLOY.md` (the instance side)._

Time: ~30 minutes, mostly downloads. Needs: Windows 10/11, 16 GB RAM
recommended (8 GB minimum), ~10 GB free disk (the AI model is ~5 GB),
admin rights for installers.

## 1. System components (one-time installers)

| Component | Why | Get it |
|---|---|---|
| **Python 3.11+** | runs Capactive | python.org — tick "Add python.exe to PATH" |
| **Git** | pulls the code + updates | git-scm.com (Git Bash is what the commands below assume) |
| **Tesseract OCR** | reads scanned documents | github.com/UB-Mannheim/tesseract/wiki — add its folder (e.g. `C:\Program Files\Tesseract-OCR`) to PATH |
| **Poppler** | renders PDF pages for OCR | github.com/oschwartz10612/poppler-windows → unzip, add `...\Library\bin` to PATH |
| **Ollama** | runs the local AI model | ollama.com — installs as a background service (tray icon) |

After Ollama installs, open a terminal and pull the model once:
```
ollama pull llama3.1:8b
```
(~5 GB; wait for it to finish. It never needs re-downloading.)

## 2. Install Capactive

In Git Bash:
```bash
cd ~
git clone <repo-url> realestate_extractor      # or unzip a release
cd realestate_extractor
python -m venv venv
venv/Scripts/pip install -r requirements.txt
```

## 3. Verify the environment

```bash
venv/Scripts/python client_doctor.py
```
Every line should read PASS. Each FAIL prints its own fix — resolve them
and re-run until clean. Do not proceed with FAILs; extraction will
silently degrade (e.g. scans come through empty without Tesseract).

## 4. First run + license

```bash
venv/Scripts/python run.py
```
Open http://127.0.0.1:5000. The setup screen creates the local
organization: enter the **license key** issued by Capactive (sets the
plan and seat limits) and the first admin login. This machine now holds
an **extraction seat** on that license.

## 5. Connect to the organization's instance

On the instance (browser, as org admin): **Admin → Devices → Register**
this machine (name it descriptively — "Front desk PC", "Sarah's laptop").
Copy the token — it is shown exactly once.

Back on this machine, create `capactive_sync.ini` in the
`realestate_extractor` folder:
```
[sync]
url = https://<your-instance-domain>
token = cap_...
```
Then, **in the same sitting** (the first contact pins this machine's
fingerprint to the token):
```bash
venv/Scripts/python client_doctor.py --instance
```
Expect `instance handshake … PASS` with the device name and org shown.

## 6. Daily workflow

1. Upload / batch-process documents in the local app (File Extractor).
2. Review: the **Review Queue** links each document to its property —
   **approving there is what finalizes it** for sync.
3. Push:
   ```bash
   venv/Scripts/python sync_client.py --status   # what would go
   venv/Scripts/python sync_client.py --push     # send it
   ```
   Corrections later? Re-approve the document; the next push sends the
   update and the instance keeps the prior version in history.

## 7. Automate the push (recommended)

Nightly push via Windows Task Scheduler — run in an **Administrator**
Command Prompt (adjust the path to your install):
```
schtasks /Create /SC DAILY /ST 22:00 /TN "Capactive Sync" ^
  /TR "\"C:\Users\<you>\realestate_extractor\venv\Scripts\python.exe\" \"C:\Users\<you>\realestate_extractor\sync_client.py\" --push" ^
  /RL HIGHEST
```
Check it: `schtasks /Run /TN "Capactive Sync"` then look at the
instance's Documents page. Note: the machine must be awake — set power
options so it doesn't sleep before the scheduled time, or pick a time
it's in use.

## 8. Updating

```bash
cd ~/realestate_extractor && git pull && venv/Scripts/pip install -r requirements.txt
```
Restart `run.py` afterwards. Local databases migrate automatically.

## 9. Moving to a new machine

Install per §1–2, copy the old machine's `data/` and `uploads/` folders
into the new install, then run
`venv/Scripts/python relink_documents.py --apply` so stored file paths
point at the new location. Register the new machine as a device on the
instance (revoke the old one) and create a fresh `capactive_sync.ini` —
tokens are pinned to hardware and do not transfer.

## Troubleshooting

- **Scans come back with no text** → Tesseract/Poppler not on PATH.
  `client_doctor.py` will show it.
- **"LLM offline" in the sidebar** → Ollama service not running (check
  the tray icon) or model not pulled.
- **Sync 401** → token revoked, or this machine's fingerprint changed
  (hardware/network adapter swap). Revoke + re-register the device.
- **Sync "Cannot reach"** → instance URL wrong or the instance is down;
  open the URL in a browser to confirm.
- **Long runs stall** → the machine went to sleep. Plugged-in power plan
  with sleep set to Never during extraction.
