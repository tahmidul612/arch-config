#!/bin/bash
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "run with sudo"; exit 1; }
D=$(dirname "$(readlink -f "$0")")
install -m 755 "$D/xbox-vm-attach" /usr/local/bin/xbox-vm-attach
install -m 644 "$D/xbox-vm-attach@.service" /etc/systemd/system/xbox-vm-attach@.service
install -m 644 "$D/99-xbox-vm-attach.rules" /etc/udev/rules.d/99-xbox-vm-attach.rules
systemctl daemon-reload
udevadm control --reload
# dry run against the currently plugged-in controller (should say "already attached")
k=$(basename "$(dirname "$(grep -l '^0b12$' /sys/bus/usb/devices/*/idProduct | head -1)")")
echo "controller is USB $k"; systemctl start "xbox-vm-attach@$k.service"; journalctl -u "xbox-vm-attach@$k.service" -n 5 -o cat --no-pager
