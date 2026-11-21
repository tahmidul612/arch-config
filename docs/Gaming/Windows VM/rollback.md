# Rollback

Every change can be undone on its own. Take an XML backup before each step ([VM definition](vm-definition.md)), named after what comes next, for example:

| Backup | Taken before | Restores |
| --- | --- | --- |
| `win11-original.xml` | any change | the virt-manager VM: 4 vCPU, SPICE audio, no iGPU |
| `win11-before-igpu.xml` | the iGPU hostdev | pinned CPUs, NAT, guest agent, Xbox hostdev |
| `win11-before-pipewire-audio.xml` | PipeWire audio | SPICE audio |
| `win11-before-ds4-gadget.xml` | removing the Xbox hostdev | Xbox pad passed to the VM |
| `win11-before-looking-glass.xml` | shmem and `memballoon none` | no Looking Glass device |

Restore one with:

```bash
virsh -c qemu:///system shutdown win11 --mode agent
virsh -c qemu:///system define win11-before-igpu.xml
virsh -c qemu:///system start win11
```

The backups must be dumped with `--security-info`, or the restore drops the VNC password.

## Host

=== "iGPU back to Linux"

    ```bash
    sudo tools/win11-vm/host-rollback.sh
    reboot
    lspci -nnk -s 00:02.0 | grep 'driver in use'    # i915 (or xe) again
    ```

    It restores `/etc/default/grub` from `/etc/default/grub.pre-vfio-igpu`, removes the modprobe and mkinitcpio drop-ins and the ROM, and regenerates GRUB and the initramfs. Restore the pre-iGPU XML first, or the VM won't start.

=== "Virtual DS4"

    ```bash
    pkill -f ds4gadget.py
    virsh -c qemu:///system detach-device win11 /tmp/ds4-hostdev.xml --live
    sudo rm /etc/udev/rules.d/70-raw-gadget.rules /etc/modules-load.d/ds4gadget.conf
    sudo modprobe -r raw_gadget dummy_hcd
    ```

    To go back to the Xbox pad in the VM: `sudo mv /etc/udev/rules.d/99-xbox-vm-attach.rules.disabled /etc/udev/rules.d/99-xbox-vm-attach.rules` and restore `win11-before-ds4-gadget.xml`.

=== "Audio"

    ```bash
    systemctl --user disable --now win11-audio-sink vm-audio-acl
    rm ~/.config/systemd/user/{win11-audio-sink,vm-audio-acl}.service
    setfacl -x u:libvirt-qemu "$XDG_RUNTIME_DIR"
    ```

    Then restore `win11-before-pipewire-audio.xml`.

=== "Deskflow"

    ```bash
    sudo ufw delete allow in on virbr0 from 192.168.122.0/24 to any port 24800 proto tcp
    rm ~/.config/autostart/org.deskflow.deskflow.desktop
    sudo pacman -Rns deskflow
    rm -r ~/.config/Deskflow
    ```

## Guest

Run as administrator in the guest (or through `guest-ps.py`).

=== "Remote Play"

    ```powershell
    $g = (Get-ItemProperty HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*, HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\* | ? DisplayName -match 'Remote Play').PSChildName
    foreach ($x in @($g)) { Start-Process msiexec -ArgumentList "/x $x /qn /norestart" -Wait }
    ```

    The patched exe goes with it. The unpatched original you copied away can be put back over a fresh 8.5 install instead.

=== "Deskflow"

    ```powershell
    Stop-Service Deskflow
    $g = (Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\* | ? DisplayName -match 'Deskflow').PSChildName
    Start-Process msiexec -ArgumentList "/x $g /qn /norestart" -Wait
    Remove-Item -Recurse C:\ProgramData\Deskflow
    ```

=== "Idle shutdown"

    ```powershell
    Unregister-ScheduledTask IdleShutdown -Confirm:$false
    Remove-Item -Recurse C:\Tools\idle-shutdown
    Enable-ScheduledTask ShutdownOnInactivity      # only if you want the old idle trigger back
    ```

=== "Intel driver"

    ```powershell
    pnputil /enum-drivers | Select-String -Context 0,6 iigd_dch    # find the oemNN.inf
    pnputil /delete-driver oemNN.inf /uninstall
    ```

    Only needed if the iGPU leaves the VM for good; without the hostdev Windows just doesn't see the device.
