#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="openclaw-long-tasks.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$SERVICE_NAME"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

mkdir -p "$UNIT_DIR"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=OpenClaw Long Tasks Runtime Service
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT/src
ExecStart=$PYTHON_BIN -m long_tasks.cli service run --worker-id scheduler --agent-id main
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"
systemctl --user status "$SERVICE_NAME" --no-pager || true

echo "Installed user service: $UNIT_PATH"