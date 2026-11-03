#!/usr/bin/env bash
# Install resmon as systemd *user* units: a collector that runs whenever you're
# logged in, plus an hourly export used by the weekly/monthly Claude reviews.
#   ./install.sh            install + start
#   ./install.sh --linger   also keep logging when you're logged out
#   ./install.sh --remove   stop and remove the units (keeps the data)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PY="$(command -v python3)"

if [[ "${1:-}" == "--remove" ]]; then
  systemctl --user disable --now resmon.service resmon-export.timer resmon-push-week.timer resmon-push-month.timer 2>/dev/null || true
  rm -f "$UNIT_DIR"/resmon.service "$UNIT_DIR"/resmon-export.{service,timer} "$UNIT_DIR"/resmon-push@.service "$UNIT_DIR"/resmon-push-{week,month}.timer
  systemctl --user daemon-reload
  echo "Removed. Data kept in ${XDG_DATA_HOME:-$HOME/.local/share}/resmon"
  exit 0
fi

command -v nvidia-smi >/dev/null || echo "warning: nvidia-smi not found, GPU columns will be empty"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/resmon.service" <<UNIT
[Unit]
Description=resmon CPU/RAM/GPU usage logger
After=default.target

[Service]
ExecStart=$PY $DIR/resmon.py collect --interval 30
Restart=always
RestartSec=30
Nice=19
IOSchedulingClass=idle

[Install]
WantedBy=default.target
UNIT

cat > "$UNIT_DIR/resmon-export.service" <<UNIT
[Unit]
Description=resmon summary export

[Service]
Type=oneshot
Nice=19
ExecStart=$PY $DIR/resmon.py export --out-dir $DIR/exports
UNIT

cat > "$UNIT_DIR/resmon-export.timer" <<UNIT
[Unit]
Description=Hourly resmon summary export

[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
UNIT

# Push the finished week / month to the Claude scheduled tasks. Persistent=true
# means a push missed while the PC was off runs at the next boot.
cat > "$UNIT_DIR/resmon-push@.service" <<UNIT
[Unit]
Description=resmon push %i review to Claude
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$PY $DIR/resmon.py push %i
UNIT

cat > "$UNIT_DIR/resmon-push-week.timer" <<UNIT
[Unit]
Description=Weekly resmon review push (Monday morning)

[Timer]
OnCalendar=Mon *-*-* 07:30:00
Persistent=true
Unit=resmon-push@week.service

[Install]
WantedBy=timers.target
UNIT

cat > "$UNIT_DIR/resmon-push-month.timer" <<UNIT
[Unit]
Description=Monthly resmon review push (1st of the month)

[Timer]
OnCalendar=*-*-01 07:20:00
Persistent=true
Unit=resmon-push@month.service

[Install]
WantedBy=timers.target
UNIT

CONF="${XDG_CONFIG_HOME:-$HOME/.config}/resmon/routines.env"
if [[ ! -f "$CONF" ]]; then
  mkdir -p "$(dirname "$CONF")"
  ( umask 077; cat > "$CONF" <<'ENV'
# Tokens for the Claude scheduled tasks resmon pushes to. Generate each in
# claude.ai/code/routines -> the task -> Add another trigger -> API -> Generate token.
RESMON_WEEKLY_TRIGGER=trig_01Y4BtRtqrEGX7u3yfKjy7k7
RESMON_WEEKLY_TOKEN=
RESMON_MONTHLY_TRIGGER=trig_01TPohD312extXiWbrt9kgjM
RESMON_MONTHLY_TOKEN=
ENV
  )
  echo "Created $CONF (chmod 600). Paste the two API tokens into it."
fi

systemctl --user daemon-reload
systemctl --user enable --now resmon.service resmon-export.timer resmon-push-week.timer resmon-push-month.timer
[[ "${1:-}" == "--linger" ]] && loginctl enable-linger "$USER"

sleep 70
"$PY" "$DIR/resmon.py" status
"$PY" "$DIR/resmon.py" export --out-dir "$DIR/exports"
echo
echo "Logging every 30 s. Full report any time:  $PY $DIR/resmon.py report --days 30"
echo "Test the push once the tokens are in:       $PY $DIR/resmon.py push week"
