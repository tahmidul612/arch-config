#!/bin/bash
# Looking Glass B8-rc1 on the host: client + OBS plugin (AUR) and the IVSHMEM shared-memory file.
# Run as your normal user; paru builds as you and asks for sudo to install.
set -euo pipefail
[ "$EUID" -ne 0 ] || { echo "run as your user, not with sudo"; exit 1; }

paru -S --needed --noconfirm --skipreview looking-glass-rc obs-plugin-looking-glass-rc

# QEMU (libvirt-qemu, member of kvm) and you both need rw on the IVSHMEM file.
printf '%s\n' '# Looking Glass IVSHMEM file shared between QEMU (group kvm) and the client/OBS (your user)' \
  "f /dev/shm/looking-glass 0660 $USER kvm -" | sudo install -m644 /dev/stdin /etc/tmpfiles.d/10-looking-glass.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf

ls -l /dev/shm/looking-glass
pacman -Q looking-glass-rc obs-plugin-looking-glass-rc
ls -d /usr/lib/obs-plugins/*looking* 2>/dev/null || true
