#!/usr/bin/env bash
# Undo host-setup.sh: give the iGPU back to Linux. Run with sudo, then reboot.
# The VM side is undone separately: virsh -c qemu:///system define <your pre-iGPU XML backup>
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo $0"; exit 1; }
GRUB_BACKUP=/etc/default/grub.pre-vfio-igpu

if [[ -f "$GRUB_BACKUP" ]]; then
  cp -a "$GRUB_BACKUP" /etc/default/grub
else
  sed -i -E 's/intel_iommu=on iommu=pt //' /etc/default/grub
fi
rm -f /etc/modprobe.d/vfio-igpu.conf /etc/mkinitcpio.conf.d/20-vfio-igpu.conf /usr/local/share/vfio/Universal_noGOP_igd.rom

grub-mkconfig -o /boot/grub/grub.cfg
mkinitcpio -P
echo "Rolled back. Reboot to give the iGPU back to Linux."
