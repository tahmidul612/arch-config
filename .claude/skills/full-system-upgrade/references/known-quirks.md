# Machine-specific notes

Check `hostname` and read only the matching section. Anything a section names
that is absent on the current host means you are on a different machine: skip
that check rather than reporting it as broken.

---

# `Ollivanders` (desktop)

CachyOS desktop. Repos: `cachyos-v3`, `cachyos-core-v3`, `cachyos-extra-v3`,
`cachyos`, `core`, `extra`, `multilib` (no chaotic-aur). AUR helper: `paru`.

## Kernels: two CachyOS flavours, not `linux`

`linux-cachyos` (default) and `linux-cachyos-lts`. Both get an initramfs, so a
kernel bump rebuilds two images; `postcheck.sh` checks each by its `pkgbase`.

## Historical: stray `linux-headers`

`linux-headers` was installed without the `linux` kernel, leaving a headers-only
modules tree that DKMS errored on at every headers bump. **Resolved 2026-10-06**
by removing the headers. If preflight warns about a headers-only tree again, it
is the same shape of problem - see triage.md §3.

## DKMS: `hid-xpadneo` and `xone`

Both must rebuild for **each** of the two kernels; `dkms status` should list four
`installed` lines.

## Howdy is on sudo here too

`/etc/pam.d/sudo` has `auth sufficient pam_howdy.so` (`howdy-git`,
`python-dlib`, `python-opencv`). The Howdy notes in the `shitblade` section below
apply here too: sudo-in-tmux behaviour, never blind-merging `sudo.pacnew`, and
the `import cv2, dlib` check after opencv/protobuf/python upgrades.

## Package lists / dotfiles

The `laptop-rice` package-list refresh below is for the laptop only. Do **not**
write this desktop's package set into that repo.

---

# `shitblade` (laptop)

Razer Blade Stealth 13" (2019), i7-8565U, Intel UHD 620. Arch (rolling), i3-wm on
X11, BTRFS + zram. Repos: `core`, `extra`, `chaotic-aur`. AUR helper: `paru`
(with `Devel` enabled, so `-git` packages are checked against upstream commits -
this is why the "Looking for devel upgrades" phase can take a while).

If any check below finds the hardware/config absent, you are on a different
machine: skip that check rather than reporting it as broken.

---

## sudo authentication goes through Howdy

`/etc/pam.d/sudo` line 2: `auth sufficient /lib/security/pam_howdy.so`

`sudo` triggers webcam face recognition before falling back to a password.
Consequences during an upgrade:

- Priming with `sudo -v` up front usually succeeds silently (`Identified face as
  aryan`) with no user interaction. `upgrade.sh start` does this deliberately.
- If the sudo timestamp expires mid-run - long AUR builds do this - the next
  `sudo` re-triggers Howdy. In poor light it prints **`Failure, timeout reached`**
  and falls back to a password prompt. That line is *not* an upgrade error; it is
  Howdy giving up. The password prompt does need the user, though, so the watcher
  escalates it as `SUDO_PASSWORD`.
- sudo uses per-tty timestamps, so authenticating in one terminal does not carry
  into the tmux pane. The auth has to happen inside the session.

**Never merge `/etc/pam.d/sudo.pacnew` casually** - dropping the Howdy line breaks
face auth, and a malformed file breaks `sudo` entirely. There is a pending
`sudo.pacnew` in the backlog for exactly this reason.

## Howdy's Python stack is fragile across upgrades

Howdy needs `cv2` and `dlib`. A protobuf/opencv soname bump has previously broken
`import cv2`, which surfaces as a Howdy traceback on every `sudo` (sudo itself
still works - the module is `sufficient`, not `required`, so it falls through to
password). Verify after any upgrade touching opencv, protobuf, or python:

```bash
python -c "import cv2, dlib; print(cv2.__version__, dlib.__version__)"
```

`python-dlib-git` is an AUR devel package, so it rebuilds on most upgrades.

## DKMS: `hid-xpadneo`

The only DKMS module. It must rebuild for each new kernel; confirm with
`dkms status` plus the presence of
`/usr/lib/modules/<new-kernel>/updates/dkms/hid-xpadneo.ko.zst`.

Historical: `linux-lts-headers` was installed without `linux-lts`, producing
`==> ERROR: Missing 6.18.45-1-lts kernel modules tree` on every kernel bump.
**Resolved 2026-08-21** by removing the stray headers. If a similar error returns,
it is the same shape of problem - see triage.md §3.

## Razer fan/power control is not a package

`razer-laptop-control` is installed outside pacman (`/usr/bin/razer-daemon` is
unowned). Two **user** units run it: `fan-curve.service` and
`razercontrol.service`. They use hidraw, not a kernel module, so kernel upgrades
do not break them - but restarting them after an upgrade is a SAFE fix if
anything looks off.

**Never run `razer-cli read fan`** - it hangs indefinitely on this install. To
verify fan control, read the log instead:
```bash
journalctl --user -u fan-curve.service -n 20 --no-pager
```

## `systemd-resolved` fails, and that is fine

`systemd-resolved.service` and its two sockets have been failing since
2026-08-19 (another mDNS responder holds the port). `/etc/resolv.conf` is a plain
file, not the resolved stub, so resolution never goes through it. Confirm with
`getent hosts archlinux.org` and report it as pre-existing - it is not upgrade
damage. Only escalate if DNS actually stops resolving.

## Power profile

Efficiency-first on battery, full performance on AC. Preflight blocks an upgrade
on battery below 40%. Turbo must stay enabled in BIOS; if AC performance looks
capped near 1.8GHz after an upgrade, that is the BIOS setting, not the upgrade.

## Dotfiles repo expects package lists to stay in sync

`~/Documents/repos/laptop-rice` versions this system's configs. Its `CLAUDE.md`
asks that package lists be refreshed and committed whenever the *explicit*
package set changes:

```bash
cd ~/Documents/repos/laptop-rice
pacman -Qqe > packages/pacman-explicit.txt
pacman -Qqm > packages/aur-packages.txt
git diff --stat packages/
```

A routine upgrade changes versions, not the explicit set, so these files usually
come out identical - check before offering to commit, and skip the offer when
there is no diff. Commit style is conventional-commits with a `packages:` prefix.

## Boot chain

GRUB (0s timeout) → greetd → startx → i3. A broken initramfs gives you no menu to
fall back to at that timeout, so treat mkinitcpio failures as blocking and verify
`/boot/vmlinuz-linux` and `/boot/initramfs-linux.img` before recommending a reboot.
