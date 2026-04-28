# Deployment guide

End-to-end instructions for deploying the Allegro bot to a Hetzner VPS with
GitHub Actions auto-deploy.

## 1. Provision a VPS (Hetzner Cloud)

1. Create account: https://accounts.hetzner.com
2. Console → **Add Server**:
   - Location: **Helsinki** or **Falkenstein** (EU IP recommended for Allegro)
   - Image: **Ubuntu 24.04**
   - Type: **CX22** (2 vCPU, 4 GB, ~4 €/mo)
   - SSH key: paste your public key (so you can `ssh root@<ip>`)
   - Name: e.g. `allegro-bot`
3. Wait ~30 s, copy the IPv4 address.
4. SSH in: `ssh root@<ip>` — accept fingerprint.

## 2. Push code to GitHub (private repo)

On your local machine:

```bash
cd C:/Users/deivi/Desktop/Projektai/bonideco_alegro_auto
git init
git add -A
git commit -m "Initial commit"
gh repo create bonideco-marketplace-bot --private --source=. --push
```

(Or create the repo manually on github.com, then `git remote add origin ...` + `git push`.)

## 3. First-time VPS setup

On the VPS (as `root`):

```bash
# Get the setup script straight from your repo
curl -L https://raw.githubusercontent.com/<YOUR_USER>/<YOUR_REPO>/main/deploy/setup_vps.sh -o setup_vps.sh
# Or: scp deploy/setup_vps.sh root@<ip>:/root/

chmod +x setup_vps.sh
REPO_URL=git@github.com:<YOUR_USER>/<YOUR_REPO>.git ./setup_vps.sh
```

Notes:
- The script creates user `botuser`, clones the repo, sets up venv, installs systemd unit, configures sudoers and firewall.
- For private repo cloning, you'll need a **deploy key** on `botuser` (see step 4) OR use HTTPS+token.

## 4. Add SSH deploy key for the repo (so VPS can `git pull`)

On the VPS, as botuser:

```bash
sudo -u botuser ssh-keygen -t ed25519 -N "" -f /home/botuser/.ssh/id_ed25519
sudo cat /home/botuser/.ssh/id_ed25519.pub
```

Copy the public key, paste into GitHub → repo → **Settings → Deploy keys → Add** (read-only is enough).

Then test:
```bash
sudo -u botuser ssh -T git@github.com   # should say "Hi <user>/<repo>! You've successfully authenticated"
```

If repo was cloned over HTTPS in step 3, switch remote:
```bash
sudo -u botuser git -C /home/botuser/bonideco_alegro_auto remote set-url origin git@github.com:<YOUR_USER>/<YOUR_REPO>.git
```

## 5. Configure .env

```bash
sudo -u botuser nano /home/botuser/bonideco_alegro_auto/.env
```

Fill at minimum:
- `ALLEGRO_EMAIL`, `ALLEGRO_PASSWORD` (placeholder is fine — we use cookies via Telegram)
- `BROWSER_USER_AGENT`, `BROWSER_SEC_CH_UA`, `BROWSER_SEC_CH_UA_PLATFORM`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_IDS`

Save & exit. Permissions are already 600 (only botuser can read).

## 6. Start the service

```bash
systemctl start allegro-bot
systemctl status allegro-bot     # should show "active (running)"
journalctl -u allegro-bot -f     # tail logs (Ctrl+C to exit)
```

In Telegram open your bot, send `/start` — should reply.

## 7. Configure GitHub Actions auto-deploy

On your local machine:
1. Generate a dedicated SSH keypair for CI:
   ```bash
   ssh-keygen -t ed25519 -N "" -f ./ci_key -C "github-actions-deploy"
   ```
2. Add `ci_key.pub` content to VPS user `botuser`:
   ```bash
   ssh root@<ip>
   sudo -u botuser bash -c 'echo "<paste pubkey>" >> /home/botuser/.ssh/authorized_keys'
   sudo chmod 600 /home/botuser/.ssh/authorized_keys
   ```
3. On GitHub repo → **Settings → Secrets and variables → Actions → New secret**:
   - `VPS_HOST` = your VPS IPv4
   - `VPS_USER` = `botuser`
   - `SSH_PRIVATE_KEY` = full content of `./ci_key` (not `.pub`)
   - `VPS_PORT` = `22` (optional, defaults to 22)
4. **Delete `ci_key` from your local machine** after copying the secret to GitHub.

Now any push to `main` triggers a deploy. The workflow:
- SSHs into VPS as `botuser`
- `git fetch + reset --hard origin/main` (preserves nothing local)
- `pip install -r requirements-prod.txt`
- `sudo systemctl restart allegro-bot`

## 8. Daily ops cheat-sheet

```bash
# Service
systemctl status allegro-bot
systemctl restart allegro-bot
systemctl stop allegro-bot

# Live logs
journalctl -u allegro-bot -f

# Last 100 log lines
journalctl -u allegro-bot -n 100 --no-pager

# Edit .env (requires restart after)
sudo -u botuser nano /home/botuser/bonideco_alegro_auto/.env
sudo systemctl restart allegro-bot

# Force a deploy from GitHub (without a code change)
gh workflow run deploy.yml          # via gh CLI
# or click "Run workflow" on the Actions tab
```

## 9. First-time cookie upload

The bot has no cookies until you send them via Telegram:
1. Login to Allegro Sales Center in your local Chrome (the same Chrome whose UA matches `BROWSER_USER_AGENT` in `.env`).
2. Cookie-Editor extension → Export → JSON → save as `cookies.json`.
3. In Telegram chat with the bot:
   - `/upload_cookies` → upload the file
   - `/health` → confirms the cookies work end-to-end

If `/health` returns "Datadome blocked" from VPS:
- Cookies might be IP-bound. Try uploading fresh cookies directly after exporting (within minutes).
- If consistently blocked from VPS, you may need to set up Playwright with persistent profile on the VPS too — a manual one-time login. Cross that bridge if it happens.

## 10. Backups & data retention

- `output/run_*.xlsx` — Excel logs of every run; `/runs` and `/download <id>` retrieve them via Telegram. They live forever on the VPS unless you prune.
- `logs/*.log` — text logs (loguru). Rotation is daily, retention 30 days.
- `storage/state.json`, `storage/storage_state.json` — bot state + cookies. **Don't commit to git** (already in `.gitignore`).
- Optional: add a weekly cron to back up `output/` and `storage/` to another location.
