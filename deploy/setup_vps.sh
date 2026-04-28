#!/usr/bin/env bash
# First-time VPS setup for the Allegro bot.
# Run as root on a fresh Ubuntu 24.04 server.
# Idempotent — safe to re-run.

set -euo pipefail

REPO_URL="${REPO_URL:-}"   # set via env or prompt
USERNAME="botuser"
HOME_DIR="/home/$USERNAME"
APP_DIR="$HOME_DIR/bonideco_marketplace_bot"

echo "==> Checking root"
if [ "$EUID" -ne 0 ]; then
    echo "Run as root" >&2
    exit 1
fi

echo "==> System update"
apt-get update -y
apt-get install -y --no-install-recommends \
    git python3.12 python3.12-venv python3-pip \
    ca-certificates curl ufw

echo "==> Creating $USERNAME"
if ! id "$USERNAME" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$USERNAME"
fi
mkdir -p "$HOME_DIR/.ssh"
chmod 700 "$HOME_DIR/.ssh"
chown -R "$USERNAME:$USERNAME" "$HOME_DIR/.ssh"

if [ -z "$REPO_URL" ]; then
    read -rp "GitHub repo SSH URL (git@github.com:user/repo.git): " REPO_URL
fi

echo "==> Cloning repo (skipped if already exists)"
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$USERNAME" git clone "$REPO_URL" "$APP_DIR"
else
    echo "Repo already exists at $APP_DIR — pulling latest"
    sudo -u "$USERNAME" git -C "$APP_DIR" pull --ff-only
fi

echo "==> Creating Python venv + installing deps"
sudo -u "$USERNAME" python3.12 -m venv "$APP_DIR/.venv"
sudo -u "$USERNAME" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$USERNAME" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-prod.txt"

echo "==> Preparing .env (if not exists)"
if [ ! -f "$APP_DIR/.env" ]; then
    sudo -u "$USERNAME" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    chown "$USERNAME:$USERNAME" "$APP_DIR/.env"
    echo
    echo "  ⚠ Created $APP_DIR/.env from template."
    echo "  Edit it before starting the bot:"
    echo "      sudo -u $USERNAME nano $APP_DIR/.env"
    echo
fi

echo "==> Installing systemd unit"
cp "$APP_DIR/deploy/allegro-bot.service" /etc/systemd/system/allegro-bot.service
systemctl daemon-reload
systemctl enable allegro-bot

echo "==> Configuring sudoers for deploy restart"
SUDOERS_FILE="/etc/sudoers.d/allegro-bot"
cat > "$SUDOERS_FILE" <<EOF
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl restart allegro-bot, /bin/systemctl status allegro-bot, /bin/systemctl stop allegro-bot, /bin/systemctl start allegro-bot
EOF
chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"

echo "==> Basic firewall (allow SSH only)"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable

echo
echo "============================================================"
echo "  VPS basic setup done."
echo
echo "  NEXT STEPS:"
echo "    1. Edit .env with credentials:"
echo "         sudo -u $USERNAME nano $APP_DIR/.env"
echo "    2. Try a dry run:"
echo "         sudo -u $USERNAME $APP_DIR/.venv/bin/python $APP_DIR/bot.py"
echo "       (Ctrl+C after you confirm bot connects to Telegram.)"
echo "    3. Start as a service:"
echo "         systemctl start allegro-bot"
echo "         systemctl status allegro-bot"
echo "    4. View logs live:"
echo "         journalctl -u allegro-bot -f"
echo "============================================================"
