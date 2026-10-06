# Triage catalogue: what upgrade findings mean and how to fix them

Each entry gives the signal, what it actually means, and the fix with its risk
tier. Tiers drive the fix policy in SKILL.md:

- **SAFE** - reversible, no config or package changes. Apply it, then report it.
- **ASK** - removes packages, edits `/etc`, or touches boot/kernel/PAM/auth.
  Propose the exact command and wait for a decision.

## Contents
1. Benign noise you should not report as problems
2. `.pacnew` / `.pacsave` files
3. DKMS failures
4. Kernel, initramfs and boot
5. Missing shared libraries and soname bumps
6. Failed systemd units
7. Orphaned packages
8. Keyring and signature errors
9. AUR build failures
10. Interrupted or partial upgrades
11. Python / Perl ecosystem breakage

---

## 1. Benign noise you should not report as problems

Reporting these as findings trains the user to ignore your reports, which is
worse than missing something. `analyze-log.sh` already filters them; this list
exists so you recognise them if you read the raw log directly.

| Line | Why it's noise |
|---|---|
| `==> WARNING: Using existing $srcdir/ tree` | makepkg reusing a cached build dir |
| `looking for conflicting packages...` | pacman status line, not a conflict |
| `:: Calculating conflicts / inner conflicts` | paru status line |
| `conflicts=(...)` | text inside a PKGBUILD being displayed |
| `configure.ac:NN: warning: ...`, `autoreconf: export WARNINGS=` | autotools chatter |
| `readelf: Warning: Gap in build notes` | debug-symbol packaging |
| `Error while writing index for ...: No debugging symbols` | makepkg debug package, benign |
| `Failure, timeout reached` right before a `[sudo] password` prompt | Howdy face-auth timed out and fell back to password |
| `warning: directory permissions differ on ...` | a package ships different dir perms than on disk; cosmetic unless it's on a security-relevant path |

Note the log contains raw `SI`/`SO` control bytes mid-line. Strip C0 controls
before matching or your patterns will silently fail to match.

---

## 2. `.pacnew` / `.pacsave` files

**Signal:** `warning: /etc/foo installed as /etc/foo.pacnew`

**Means:** you edited that config, and the package shipped a new default. Your
version is still live; the new default sits beside it unused. Ignoring these
indefinitely is how configs drift until something breaks years later.

Only report `.pacnew` files *new in this run* - compare against the preflight
baseline. A long pre-existing backlog is worth mentioning once, not re-litigating.

**Fix (ASK - edits `/etc`):**
```bash
sudo pacdiff -s          # walk each one with a diff view (uses sudoedit)
```
Handle high-stakes ones individually and read the diff before merging:
- `/etc/pam.d/*` - a bad merge here can lock the user out entirely. On this
  machine `pam.d/sudo` carries the Howdy line; losing it breaks face auth,
  and a malformed file breaks `sudo` outright. Keep a root shell open while editing.
- `/etc/ssh/sshd_config` - check `PermitRootLogin`/`PasswordAuthentication`
  before restarting `sshd`, especially if the box is reachable remotely.
- `/etc/mkinitcpio.conf` - a merge that drops a HOOK produces an unbootable
  initramfs. Always `mkinitcpio -P` and confirm success before rebooting.
- `/etc/fstab`, `/etc/default/grub` - regenerate and verify, never blind-merge.

---

## 3. DKMS failures

**Signal:** `==> ERROR: Missing <version> kernel modules tree for module <mod>`

**Means:** DKMS tried to build against a modules tree that has headers but no
kernel. Usually `linux-<variant>-headers` is installed without `linux-<variant>`.
This does **not** fail the upgrade - the transaction is already committed - but it
recurs on every kernel bump. Confirm the module *did* build for the real kernel
before treating it as harmless:
```bash
dkms status
find /usr/lib/modules/$(pacman -Q linux | awk '{print $2}' | sed 's/\./\.arch/;s/-/-arch1-/') -path '*/updates/dkms/*' -name '*.ko*'
```

**Fix (ASK - removes a package):** either drop the stray headers or install the
matching kernel.
```bash
pacman -Qi linux-lts-headers | grep 'Required By'   # confirm nothing needs it
sudo pacman -Rs linux-lts-headers                   # or: sudo pacman -S linux-lts
```

**Signal:** module missing for the *installed* kernel after upgrade.

**Fix (SAFE - rebuild only):**
```bash
sudo dkms autoinstall -k <installed-kernel-version>
```

---

## 4. Kernel, initramfs and boot

**Reboot required** when `/usr/lib/modules/$(uname -r)` no longer exists: Arch
removed the running kernel's modules, so anything not already loaded cannot load.
Say this plainly - it is the single most consequential post-upgrade fact.

**Signal:** `==> WARNING: consolefont: no font found in configuration`
**Means:** the `consolefont` hook is in `mkinitcpio.conf` but `FONT=` is unset, so
the hook no-ops. Harmless.
**Fix (ASK - edits `/etc`):** set `FONT=` or drop the hook, then `sudo mkinitcpio -P`.

**Signal:** mkinitcpio errors, or `/boot/vmlinuz-linux` missing/stale.
**Means:** potentially unbootable. Treat as CRITICAL. Do not reboot until fixed.
**Fix (ASK):** `sudo mkinitcpio -P` and read the output; check `/boot` free space
first, since a full `/boot` is the usual cause.

---

## 5. Missing shared libraries and soname bumps

**Signal:** an app fails with `error while loading shared libraries: libX.so.N`,
or `ldd` on a binary reports `not found`.

**Means:** a library bumped its soname and something still links the old one -
typically an AUR package built against the previous version, or a Python
extension module.

**Diagnose:**
```bash
ldd /usr/bin/<binary> | grep 'not found'
sudo find /usr/lib -name 'libX.so*'
```

**Fix (ASK - rebuilds/installs packages):** rebuild the dependent AUR package:
```bash
paru -S --rebuild <pkg>
```
As a last resort, restoring the old `.so` from `/var/cache/pacman/pkg` works, but
it is a shim: the correct fix is rebuilding against the new soname.

Processes shown by `lsof | grep DEL.*\.so` are merely *running* against deleted
libraries - normal after any upgrade, cleared by a reboot. Do not report these as
breakage.

---

## 6. Failed systemd units

Always check *when* the unit started failing before blaming the upgrade:
```bash
systemctl status <unit> --no-pager
journalctl -u <unit> -n 30 --no-pager -o short-iso
```
A failure timestamped days before the upgrade is pre-existing - say so explicitly
rather than presenting it as new damage.

**Fix (SAFE for user units and stateless daemons):**
```bash
systemctl --user restart <unit>
sudo systemctl restart <unit>
```
**Fix (ASK)** if the unit is `systemd-networkd`, `NetworkManager`, `sshd`, or
anything else that could drop your remote access, or if fixing it means editing
config.

Before reporting a name-resolution unit as broken, verify resolution actually
fails - `getent hosts archlinux.org`. If `/etc/resolv.conf` is a plain file rather
than the resolved stub, `systemd-resolved` may not be in the path at all and its
failure is inconsequential.

---

## 7. Orphaned packages

**Signal:** `pacman -Qdtq` lists packages.

**Means:** installed as dependencies, now depended on by nothing. Many are
deliberate - build toolchains, `rustup`, scientific Python stacks - so this is a
list to review, never to pipe into `pacman -Rns`.

**Fix (ASK - removes packages):** present the list, let the user pick.
```bash
pacman -Qi <pkg> | grep -E 'Description|Required By|Install Reason'
sudo pacman -Rns <chosen packages>
```
Mass-removing orphans is a classic way to uninstall something you needed.

---

## 8. Keyring and signature errors

**Signal:** `signature from "..." is unknown trust` / `is marked as expired` /
`invalid or corrupted package (PGP signature)`

**Means:** the local keyring is stale, or a package was truncated in transit.

**Fix (SAFE - refreshes keys/cache, no config change):**
```bash
sudo pacman-key --init && sudo pacman-key --populate archlinux
sudo pacman -Sy archlinux-keyring        # then re-run the upgrade
sudo rm /var/cache/pacman/pkg/<bad>.pkg.tar.zst   # if a single package is corrupt
```
If a *new* AUR key needs importing, that is an ASK: it is a trust decision.

---

## 9. AUR build failures

**Signal:** `==> ERROR: A failure occurred in build()` / `prepare()` / `package()`

**Means:** one AUR package failed. Repo packages already installed fine - the
system is not broken, one program is just not updated.

**Diagnose:** read upward from the error for the first compiler/script error; the
last lines are usually just the abort cascade.

Common causes and fixes (all ASK - they rebuild or change packages):
- Stale cached sources → `paru -S --rebuild --cleanafter <pkg>`
- Dependency soname changed → rebuild the dependency first
- Upstream source moved/404 → the PKGBUILD needs updating; check the AUR comments
- Toolchain too new (common right after a GCC major bump) → wait for the
  maintainer, or pin/skip with `paru -Syu --ignore <pkg>`

Report clearly which package failed and that everything else succeeded.

---

## 10. Interrupted or partial upgrades

**Signal:** power loss or kill mid-transaction; `unable to lock database`;
mixed library versions.

**Never** run `pacman -Sy <pkg>` on its own - that is what *creates* partial
upgrades. Always complete the full `-Syu`.

**Fix (ASK):**
```bash
sudo rm /var/lib/pacman/db.lck        # ONLY after confirming no pacman is running
ps aux | grep -E 'pacman|paru'
sudo pacman -Syu                      # finish the transaction
```

---

## 11. Python / Perl ecosystem breakage

**Signal:** after a Python **minor** bump (3.13 → 3.14), `ModuleNotFoundError` for
things that worked, because `site-packages` moved to a new versioned path.

Only pip/AUR-installed modules are affected; repo packages are rebuilt by the
maintainers. A patch bump (3.14.6 → 3.14.7) does not do this.

**Check the things you know matter on this machine** rather than guessing - e.g.
Howdy's dependencies:
```bash
python -c "import cv2, dlib; print(cv2.__version__, dlib.__version__)"
```

**Fix (ASK - reinstalls packages):**
```bash
paru -S --rebuild <affected-aur-python-pkg>
pip install --user --force-reinstall <pkg>    # for pip-installed modules
```

`(20/26) Checking for old perl modules...` running is normal; it only matters if
it *reports* modules needing rebuild.
