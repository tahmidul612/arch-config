#!/bin/bash
# Prepare the host for the virtual DualShock 4 (ds4gadget.py): keep the Xbox pad on the host,
# load the virtual-USB modules, and let the calling user open /dev/raw-gadget without sudo.
# Run with sudo from your normal account: sudo ./ds4-host-prep.sh
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "run with sudo"; exit 1; }
OWNER=${SUDO_USER:?run this with sudo from your normal account}
if [ -f /etc/udev/rules.d/99-xbox-vm-attach.rules ]; then
  mv /etc/udev/rules.d/99-xbox-vm-attach.rules /etc/udev/rules.d/99-xbox-vm-attach.rules.disabled
  echo "disabled Xbox->VM auto-attach rule"
fi
echo "KERNEL==\"raw-gadget\", OWNER=\"$OWNER\", MODE=\"0600\"" > /etc/udev/rules.d/70-raw-gadget.rules
udevadm control --reload
modprobe dummy_hcd
modprobe raw_gadget
udevadm trigger --name-match=raw-gadget 2>/dev/null || true
sleep 1
chown "$OWNER" /dev/raw-gadget; chmod 600 /dev/raw-gadget
ls -l /dev/raw-gadget; ls /sys/class/udc/
