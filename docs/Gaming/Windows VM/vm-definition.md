# VM definition

The libvirt domain `win11` runs on `qemu:///system`. It started as an ordinary virt-manager Windows 11 VM (q35, OVMF with Secure Boot, SATA disk, SPICE, 4 vCPU, 8 GiB). Each section below is one change to it. The complete result, with the machine-specific values removed, is [`tools/win11-vm/xml/win11.xml`](https://github.com/tahmidul612/arch-config/blob/main/tools/win11-vm/xml/win11.xml).

!!! danger "Always dump with `--security-info`"
    A plain `virsh dumpxml` leaves out the VNC password. If you edit that dump and `virsh define` it, the password is silently gone. Every backup and edit here uses:

    ```bash
    virsh -c qemu:///system dumpxml win11 --inactive --security-info > win11-backup.xml
    ```

    Take a backup like this before each section, so each change can be undone on its own ([Rollback](rollback.md)).

!!! warning "zsh and `virsh` in a variable"
    `C="virsh -c qemu:///system"; $C start win11` fails under zsh, because zsh does not split `$C` into words. The error is "no such file or directory" and a wait loop around it just times out. Write the command out in full in scripts.

## Using the reference XML

The reference file has placeholders: `VM_DISK` (your qcow2), `VIRTIO_WIN_ISO` (or delete that cdrom), and `UID` in the audio `runtimeDir` (`id -u` of the desktop user). It has no UUID or MAC; libvirt generates them on `define`. It has no VNC password either: add `passwd='...'` to the VNC `<graphics>` or remove that element.

```bash
cp tools/win11-vm/xml/win11.xml /tmp/win11.xml   # edit the placeholders
virt-xml-validate /tmp/win11.xml
virsh -c qemu:///system define /tmp/win11.xml
```

Do this only for a new VM. For an existing one, apply the sections below to its own XML (`virsh edit win11` or dump, edit, define).

## CPU and memory

```xml
<vcpu placement='static'>8</vcpu>
<cputune>
  <vcpupin vcpu='0' cpuset='8'/>   <!-- ... one line per vCPU, 0-7 to host CPUs 8-15 -->
  <vcpupin vcpu='7' cpuset='15'/>
  <emulatorpin cpuset='16-19'/>
</cputune>
<cpu mode='host-passthrough' check='none' migratable='off'>
  <topology sockets='1' dies='1' clusters='1' cores='4' threads='2'/>
  <maxphysaddr mode='passthrough' limit='39'/>
</cpu>
```

- 8 vCPU in one socket (4 cores, 2 threads). The original 4 vCPU showed up as 4 one-core sockets. Windows client editions use at most 2 sockets, so one socket is the safe layout.
- Pinning: on the i7-12700K, host CPUs 0-15 are the 8 P-cores with their hyperthreads and 16-19 are E-cores. The vCPUs get 4 P-cores (8-15) and the QEMU emulator threads get E-cores (16-19). That is the reasoning; nobody measured whether pinning made a difference.
- `maxphysaddr limit='39'` is the IOMMU width from [Host preparation](host-prep.md#4-read-the-iommu-address-width).
- Memory stays at 8 GiB. `memballoon` is `none` (a Looking Glass requirement).

Check from the host after a start:

```bash
virsh -c qemu:///system vcpupin win11
virsh -c qemu:///system emulatorpin win11
```

For idle and in-game CPU baselines, and for alerts when the VM sits at high CPU for hours, see [resmon](https://github.com/tahmidul612/arch-config/blob/main/tools/resmon/README.md). Measured on 2026-11-21: an idle guest uses 10-25% of one host core; Remote Play plus guest OBS recording used 140-160%.

## iGPU passthrough

Three pieces: free guest slot 2, pass the device there, and give QEMU the OpRegion.

Free guest slot `00:02.0` first. In UPT mode the iGPU has to sit at the same address in the guest as on the host. virt-manager had put 8 `pcie-root-port` controllers at slot `0x02`. Move them to a free slot (`0x04` here; check with `grep "slot='0x04'"` that nothing uses it) by changing only `slot='0x02'` to `slot='0x04'` in their `<address>` lines. Windows later shows the old ports as phantom PCI bridges; they are harmless.

Then add the hostdev:

```xml
<hostdev mode='subsystem' type='pci' managed='yes'>
  <source>
    <address domain='0x0000' bus='0x00' slot='0x02' function='0x0'/>
  </source>
  <alias name='ua-igpu'/>
  <rom file='/usr/local/share/vfio/Universal_noGOP_igd.rom'/>
  <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0'/>
</hostdev>
```

Finally, the OpRegion override. libvirt has no element for it, so it goes through the QEMU namespace. The root element must declare it:

```xml
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  ...
  <qemu:override>
    <qemu:device alias='ua-igpu'>
      <qemu:frontend>
        <qemu:property name='x-igd-opregion' type='bool' value='true'/>
      </qemu:frontend>
    </qemu:device>
  </qemu:override>
</domain>
```

Keep the emulated display: `virtio-vga` with SPICE (and the local VNC) stays in the XML. It shows the boot screens and is the console if the iGPU output fails. Nothing else uses it.

Check: the VM starts (QEMU accepted the device), and Windows lists a "Video Controller" without a driver until [the Intel driver](windows-guest.md#intel-graphics-driver) is installed.

!!! note "Not needed here"
    The [GOPv17 ROM](https://github.com/LongQT-sea/intel-igpu-passthru/releases/tag/v0.1) and setting the firmware's primary display to IGD are the documented fallbacks for Code 43 or a black screen. Neither was needed: the noGOP ROM worked first time.

## Network: libvirt NAT

```xml
<interface type='network'>
  <source network='default'/>
  <model type='virtio'/>
</interface>
```

The guest is on `192.168.122.0/24` and the host is `192.168.122.1` on `virbr0`. Remote Play works over it. macvtap on the wired NIC was tried to avoid double NAT and failed (see [Troubleshooting](troubleshooting.md#what-was-tried)).

## Guest agent

```xml
<channel type='unix'>
  <target type='virtio' name='org.qemu.guest_agent.0'/>
</channel>
```

The guest needs the QEMU guest agent from the virtio-win ISO. Every guest change in this guide is made through it from the host ([Windows guest](windows-guest.md#running-commands-in-the-guest)), and `virsh shutdown --mode agent` gives a clean shutdown in a few seconds.

```bash
virsh -c qemu:///system qemu-agent-command win11 '{"execute":"guest-ping"}'   # {"return":{}}
```

## Audio

The SPICE audio backend was replaced by QEMU's PipeWire backend. The XML is below; the host side (an ACL so QEMU can reach your PipeWire socket) is on [Audio](audio.md).

```xml
<sound model='ich9'/>
<audio id='1' type='pipewire' runtimeDir='/run/user/UID'>
  <input streamName='win11-mic' latency='20000'/>
  <output streamName='win11' latency='20000'/>
</audio>
```

`runtimeDir` is the desktop user's `$XDG_RUNTIME_DIR` (`UID` = `id -u`). The change takes effect at the next full VM stop and start.

## Shared memory for Looking Glass

```xml
<memballoon model='none'/>
<shmem name='looking-glass'>
  <model type='ivshmem-plain'/>
  <size unit='M'>64</size>
</shmem>
```

The size depends on the guest's desktop resolution: 64 MiB fits 2560x1440, and a 3840x2160 desktop needs 128 MiB. The host file `/dev/shm/looking-glass` and the guest side are part of the Looking Glass display setup.

## Restarting so changes apply

A device change applies only after a full stop and start. A reboot from inside Windows is not enough.

```bash
virsh -c qemu:///system shutdown win11 --mode agent
virsh -c qemu:///system domstate win11          # wait for "shut off"
virsh -c qemu:///system start win11
```
