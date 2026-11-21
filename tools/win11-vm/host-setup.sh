#!/usr/bin/env bash
# Hand the Intel iGPU (UHD 770, 8086:4680 at 00:02.0) to vfio-pci at boot so the
# win11 VM can own it. Run with sudo, then reboot. Undo with host-rollback.sh.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo $0"; exit 1; }

ROM_URL=https://github.com/LongQT-sea/intel-igpu-passthru/releases/download/v0.1/Universal_noGOP_igd.rom
ROM_SHA=d1c95f6062eba9d3164263bf11626d5d958d82439525d41570256e5a72d9b18f
ROM_DST=/usr/local/share/vfio/Universal_noGOP_igd.rom
GRUB_BACKUP=/etc/default/grub.pre-vfio-igpu

# 0. Sanity checks
lspci -n -s 00:02.0 | grep -q '8086:4680' || { echo "00:02.0 is not the UHD 770 (8086:4680); aborting"; exit 1; }
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
curl -fsSL -o "$tmp" "$ROM_URL"
echo "$ROM_SHA  $tmp" | sha256sum -c --quiet || { echo "ROM checksum mismatch; aborting"; exit 1; }

# 1. Back up the one file we edit in place (only the first time)
[[ -f "$GRUB_BACKUP" ]] || cp -a /etc/default/grub "$GRUB_BACKUP"

# 2. Kernel options: turn on the IOMMU
if ! grep -q 'intel_iommu=on' /etc/default/grub; then
  sed -i -E "s/^(GRUB_CMDLINE_LINUX_DEFAULT=['\"])/\1intel_iommu=on iommu=pt /" /etc/default/grub
fi
grep '^GRUB_CMDLINE_LINUX_DEFAULT' /etc/default/grub

# 3. Bind the iGPU to vfio-pci before i915/xe can claim it
cat > /etc/modprobe.d/vfio-igpu.conf <<'EOF'
# iGPU passthrough to the win11 VM
options vfio-pci ids=8086:4680
softdep i915 pre: vfio-pci
softdep xe pre: vfio-pci
EOF
cat > /etc/mkinitcpio.conf.d/20-vfio-igpu.conf <<'EOF'
# iGPU passthrough: load vfio early so it claims 8086:4680 before i915 does
MODULES+=(vfio_pci vfio vfio_iommu_type1)
EOF

# 4. Install the option ROM where QEMU can read it
install -Dm644 "$tmp" "$ROM_DST"

# 5. Regenerate boot files
grub-mkconfig -o /boot/grub/grub.cfg
mkinitcpio -P

echo
echo "Done. Reboot, then check: lspci -nnk -s 00:02.0  (Kernel driver in use: vfio-pci)"
