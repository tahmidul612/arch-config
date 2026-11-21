#!/bin/bash
# Load the modules the virtual DS4 needs at every boot (dummy_hcd = fake USB controller, raw_gadget = userspace USB device).
set -euo pipefail
printf '%s\n' '# virtual DualShock 4 for the win11 VM (~/.local/lib/ds4-vm)' dummy_hcd raw_gadget | sudo install -m644 /dev/stdin /etc/modules-load.d/ds4-gadget.conf
sudo modprobe dummy_hcd; sudo modprobe raw_gadget
cat /etc/modules-load.d/ds4-gadget.conf; ls -l /dev/raw-gadget
