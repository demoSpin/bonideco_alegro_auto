#!/usr/bin/env bash
# First-time setup for the Allegro bot.
# SAFE for shared / already-in-use VPS — does NOT touch firewall, does NOT
# install system Python if a recent one already exists, does NOT modify other
# users' services.
#
# Run as root.

set -euo pipefail

REPO_URL="${REPO_URL:-}"
USERNAME="${USERNAME:-botuser}"
HOME_DIR="/home/$USERNAME"
APP_DIR="$HOME_DIR/bonideco_alegro_auto"

echo "==> Sanity check (running as root)"
if [ "$EUID" -ne 0 ]; then
    echo "Run as root" >&2
    exit 1
fi

echo "==> Detecting a recent Python 3 (>= 3.10 needed)"
PYTHON_BIN=""
for v in python3.12 python3.11 python3.10; do
    if command -v "$v" >/dev/null 2>&1; then
        PYTHON_BIN="$v"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo
    echo "Python 3.10+ was not found. Install one of:"
    echo "  apt update && apt install -y python3.12 python3.12-venv"
    echo "  apt update && apt install -y python3.11 python3.11-venv"
    echo "Then re-run this script."
    exit 1
fi
echo "Using $PYTHON_BIN ($($PYTHON_BIN --version))"

echo "==> Ensuring git + venv support"
PKGS=()
command -v git >/dev/null 2>&1 || PKGS+=("git")
# `-m venv --help` always works (module is in stdlib) but creating a venv
# fails without the `${PY}-venv` apt package (which ships ensurepip).
# Check ensurepip directly.
"$PYTHON_BIN" -c "import ensurepip" >/dev/null 2>&1 || PKGS+=("${PYTHON_BIN}-venv")
if [ "${#PKGS[@]}" -gt 0 ]; then
    echo "Installing missing packages: ${PKGS[*]}"
    apt-get update -y
    apt-get install -y --no-install-recommends "${PKGS[@]}"
fi

echo "==> Creating user $USERNAME (idempotent)"
if ! id "$USERNAME" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$USERNAME"
fi
mkdir -p "$HOME_DIR/.ssh"
chmod 700 "$HOME_DIR/.ssh"
touch "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"
chown -R "$USERNAME:$USERNAME" "$HOME_DIR/.ssh"

if [ -z "$REPO_URL" ]; then
    read -rp "GitHub repo URL (https://... or git@github.com:...): " REPO_URL
fi

echo "==> Cloning repo"
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$USERNAME" git clone "$REPO_URL" "$APP_DIR"
else
    echo "Repo already at $APP_DIR — pulling latest"
    sudo -u "$USERNAME" git -C "$APP_DIR" pull --ff-only
fi

echo "==> Creating Python venv + installing deps"
sudo -u "$USERNAME" "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
sudo -u "$USERNAME" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$USERNAME" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-prod.txt"

echo "==> Preparing .env (if not exists)"
if [ ! -f "$APP_DIR/.env" ]; then
    sudo -u "$USERNAME" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    chown "$USERNAME:$USERNAME" "$APP_DIR/.env"
    echo
    echo "  ⚠ Created $APP_DIR/.env from template — EDIT IT before starting:"
    echo "      sudo -u $USERNAME nano $APP_DIR/.env"
    echo
fi

echo "==> Installing systemd unit"
SERVICE_SRC="$APP_DIR/deploy/allegro-bot.service"
SERVICE_DST="/etc/systemd/system/allegro-bot.service"
if [ "$USERNAME" != "botuser" ]; then
    sed "s|botuser|$USERNAME|g" "$SERVICE_SRC" > "$SERVICE_DST"
else
    cp "$SERVICE_SRC" "$SERVICE_DST"
fi
systemctl daemon-reload
systemctl enable allegro-bot

echo "==> sudoers entry for restart-without-password"
SUDOERS_FILE="/etc/sudoers.d/allegro-bot"
cat > "$SUDOERS_FILE" <<EOF
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl restart allegro-bot, /bin/systemctl status allegro-bot, /bin/systemctl stop allegro-bot, /bin/systemctl start allegro-bot
EOF
chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"

echo
echo "============================================================"
echo "  Bot installed (user: $USERNAME, app: $APP_DIR)"
echo "  Existing services on this VPS: NOT TOUCHED"
echo "  Firewall: NOT MODIFIED — open SSH yourself if needed"
echo
echo "  NEXT STEPS:"
echo "    1. Edit .env:"
echo "         sudo -u $USERNAME nano $APP_DIR/.env"
echo "    2. Start as a service:"
echo "         systemctl start allegro-bot"
echo "         systemctl status allegro-bot"
echo "    3. Tail logs:"
echo "         journalctl -u allegro-bot -f"
echo "============================================================"
