# Host preparation

The Intel UHD 770 (PCI `00:02.0`, `8086:4680`) goes to the VM. Linux keeps the NVIDIA card for the desktop. This page gives the iGPU to `vfio-pci` at boot so no host driver touches it.

!!! info "Hardware this was done on"
    i7-12700K (UHD 770 iGPU), MSI board, RTX 3080 driving the Linux desktop, CachyOS with GRUB and mkinitcpio, libvirt 12.9, QEMU 11.1. The iGPU's HDMI port goes to a monitor; that is only needed for the [HDMI display mode](README.md#how-it-fits-together).

## 1. Check the iGPU and the IOMMU

What: confirm the device ID and that the firmware exposes an IOMMU (Intel VT-d).
Why: `host-setup.sh` refuses to run on anything but `8086:4680`, and passthrough needs VT-d.

```bash
lspci -nn -s 00:02.0                          # ... UHD Graphics 770 [8086:4680]
ls /sys/firmware/acpi/tables/DMAR             # exists if VT-d is on in the firmware
```

The CachyOS kernel does not turn the IOMMU on by default (`CONFIG_INTEL_IOMMU_DEFAULT_ON` is not set), so the kernel option in the next step is required.

## 2. Bind the iGPU to vfio-pci

What: [`host-setup.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/host-setup.sh) does five things:

1. Downloads `Universal_noGOP_igd.rom` from the [LongQT-sea/intel-igpu-passthru v0.1 release](https://github.com/LongQT-sea/intel-igpu-passthru/releases/tag/v0.1) and checks its sha256.
2. Backs up `/etc/default/grub` to `/etc/default/grub.pre-vfio-igpu` and adds `intel_iommu=on iommu=pt`.
3. Writes `/etc/modprobe.d/vfio-igpu.conf` (`options vfio-pci ids=8086:4680`, plus softdeps so `vfio-pci` loads before `i915` and `xe`).
4. Writes `/etc/mkinitcpio.conf.d/20-vfio-igpu.conf` so the vfio modules are in the initramfs.
5. Installs the ROM to `/usr/local/share/vfio/` and regenerates `grub.cfg` and the initramfs.

Why the ROM: QEMU's IGD passthrough has two modes. Legacy mode is limited to Gen 6-9 iGPUs on QEMU 10.1+ and its `x-igd-lpc` option only works on i440fx machines, so this setup uses **UPT mode** on the q35 machine. UPT mode needs an option ROM with the iGPU's OpRegion, and the "noGOP" ROM is the one that project recommends for UPT.

```bash
cd tools/win11-vm
sudo ./host-setup.sh
reboot
```

!!! note
    The VM's boot screens never appear on the HDMI port in UPT mode. The monitor lights up only after the Windows Intel driver loads. Until then, use the virt-manager console.

## 3. Check after the reboot

```bash
grep -o 'intel_iommu=on iommu=pt' /proc/cmdline
lspci -nnk -s 00:02.0 | grep 'driver in use'     # Kernel driver in use: vfio-pci
for g in /sys/kernel/iommu_groups/*/devices/0000:00:02.0; do ls "$(dirname "$g")"; done   # only 0000:00:02.0
```

On this machine the iGPU is alone in IOMMU group 0.

## 4. Read the IOMMU address width

What: the guest's physical address width must not exceed what the IOMMU can map. Otherwise the VM fails to start with `vfio_container_dma_map ... -22`.
Why here: the value goes into the VM's `<maxphysaddr>` on the [next page](vm-definition.md).

```bash
for c in /sys/class/iommu/dmar*/intel-iommu/cap; do
  echo "$c: $(( ((0x$(cat "$c") >> 16) & 0x3f) + 1 )) bits"
done
```

That is the MGAW field, bits 21:16 of the capability register, plus one. Here both DMAR units report **39 bits**, while the CPU has 46-bit physical addresses (`grep 'address sizes' /proc/cpuinfo`), so the VM gets `limit='39'`.

!!! warning
    An earlier attempt read bits 13:8 and got 5, which is impossible. Use bits 21:16.

## Files

| File | Installed to | Purpose |
| --- | --- | --- |
| [`tools/win11-vm/host-setup.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/host-setup.sh) | `/etc/default/grub`, `/etc/modprobe.d/vfio-igpu.conf`, `/etc/mkinitcpio.conf.d/20-vfio-igpu.conf`, `/usr/local/share/vfio/Universal_noGOP_igd.rom` | iGPU to vfio-pci at boot |
| [`tools/win11-vm/host-rollback.sh`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/host-rollback.sh) | removes the above | Give the iGPU back to Linux, see [Rollback](rollback.md) |
