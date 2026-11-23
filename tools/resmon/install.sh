#!/usr/bin/env bash
# Build resmon (and qga, which VM alerts use) and install them as systemd *user* units:
# a collector that runs whenever you're logged in, an hourly export, a 15-min stuck-process
# check, and the weekly/monthly pushes to the Claude reviews.
#   ./install.sh            build, install, start
#   ./install.sh --linger   also keep logging when you're logged out
#   ./install.sh --remove   stop and remove the units (keeps the data)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/resmon"
BIN_DIR="$HOME/.local/bin"
UNITS=(resmon.service resmon-export.timer resmon-watch.timer resmon-push-week.timer resmon-push-month.timer)

if [[ "${1:-}" == "--remove" ]]; then
  systemctl --user disable --now "${UNITS[@]}" 2>/dev/null || true
  for f in "$DIR"/systemd/*; do rm -f "$UNIT_DIR/$(basename "$f")"; done
  systemctl --user daemon-reload
  echo "Removed the units. Data kept in $DATA_DIR; binaries in $BIN_DIR."
  exit 0
fi

# The workspace root is tools/, one level up.
cargo build --release --locked --manifest-path "$DIR/../Cargo.toml" -p resmon -p qga
install -Dm755 -t "$BIN_DIR" "$DIR/../target/release/resmon" "$DIR/../target/release/qga"

mkdir -p "$UNIT_DIR"
install -m644 -t "$UNIT_DIR" "$DIR"/systemd/*

CONF="${XDG_CONFIG_HOME:-$HOME/.config}/resmon/routines.env"
if [[ ! -f "$CONF" ]]; then
  mkdir -p "$(dirname "$CONF")"
  ( umask 077; cat > "$CONF" <<'ENV'
# Triggers and tokens for the Claude scheduled tasks resmon pushes to. Generate each in
# claude.ai/code/routines -> the task -> Add another trigger -> API -> Generate token.
RESMON_WEEKLY_TRIGGER=
RESMON_WEEKLY_TOKEN=
RESMON_MONTHLY_TRIGGER=
RESMON_MONTHLY_TOKEN=
# Optional: extra alert command, gets the title and body as $1 and $2.
#RESMON_ALERT_CMD=
ENV
  )
  echo "Created $CONF (mode 600). Paste the triggers and tokens into it."
fi

systemctl --user daemon-reload
systemctl --user enable --now "${UNITS[@]}"
systemctl --user restart resmon.service   # pick up a new binary
[[ "${1:-}" == "--linger" ]] && loginctl enable-linger "$USER"

sleep 35
"$BIN_DIR/resmon" status
"$BIN_DIR/resmon" export
echo
echo "Logging every 30 s. Full report any time:  resmon report --days 30"
echo "Test the push once the tokens are in:       resmon push week --dry-run"
