#!/usr/bin/env bash
# Pre-upgrade safety checks. Prints a report; exits 1 if a hard blocker is found.
# Blockers are conditions where starting a 150-package upgrade risks leaving the
# system half-upgraded and unbootable. Warnings are worth telling the user but
# do not stop the run.
set -uo pipefail

BLOCKERS=0
WARNINGS=0
block() { echo "  [BLOCK] $*"; BLOCKERS=$((BLOCKERS+1)); }
warn()  { echo "  [WARN ] $*"; WARNINGS=$((WARNINGS+1)); }
ok()    { echo "  [ok   ] $*"; }

echo "########## PREFLIGHT ##########"

# --- Power -------------------------------------------------------------------
# A multi-GB upgrade that loses power mid-transaction can corrupt the package DB.
ac=$(cat /sys/class/power_supply/A*/online 2>/dev/null | head -1)
bat=$(cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -1)
if [ "${ac:-1}" = "1" ]; then
  ok "on AC power${bat:+ (battery ${bat}%)}"
elif [ -n "${bat:-}" ] && [ "$bat" -lt 40 ]; then
  block "on battery at ${bat}% - plug in before upgrading"
else
  warn "on battery${bat:+ at ${bat}%} - plugging in is strongly advised"
fi

# --- Disk space --------------------------------------------------------------
# Package cache lands on whatever fs holds /var/cache/pacman/pkg; /boot needs
# room for a new vmlinuz + initramfs (and a fallback image if presets build one).
root_avail=$(df -BM --output=avail / | tail -1 | tr -dc '0-9')
boot_avail=$(df -BM --output=avail /boot 2>/dev/null | tail -1 | tr -dc '0-9')
if [ "${root_avail:-0}" -lt 5000 ]; then
  block "only ${root_avail}MB free on / - need ~5GB headroom for a full upgrade"
elif [ "${root_avail:-0}" -lt 12000 ]; then
  warn "${root_avail}MB free on / - tight; consider 'paccache -rk1' first"
else
  ok "${root_avail}MB free on /"
fi
if [ -n "${boot_avail:-}" ]; then
  if [ "$boot_avail" -lt 100 ]; then
    block "only ${boot_avail}MB free on /boot - kernel install will fail partway"
  elif [ "$boot_avail" -lt 300 ]; then
    warn "${boot_avail}MB free on /boot - enough for one kernel, no slack"
  else
    ok "${boot_avail}MB free on /boot"
  fi
fi

# --- Partial-upgrade / DB state ---------------------------------------------
if [ -f /var/lib/pacman/db.lck ]; then
  block "/var/lib/pacman/db.lck exists - another pacman is running (or crashed)"
else
  ok "no stale pacman db lock"
fi

# --- Keyring -----------------------------------------------------------------
# An expired/stale keyring is the classic cause of "signature is unknown trust"
# failures partway through a large download.
keyring_age_days=$(( ( $(date +%s) - $(stat -c %Y /etc/pacman.d/gnupg/trustdb.gpg 2>/dev/null || date +%s) ) / 86400 ))
if [ "$keyring_age_days" -gt 180 ]; then
  warn "pacman keyring trustdb is ${keyring_age_days} days old - if signature errors appear, run: sudo pacman-key --refresh-keys"
else
  ok "pacman keyring trustdb age ${keyring_age_days}d"
fi

# --- DKMS / kernel -----------------------------------------------------------
echo "  --- dkms / kernel ---"
echo "    running kernel : $(uname -r)"
if command -v dkms >/dev/null 2>&1; then
  dkms status 2>/dev/null | sed 's/^/    dkms: /'
  # Headers installed with no matching kernel package produce a modules tree that
  # DKMS tries (and fails) to build against on every upgrade. Flag it up front so
  # the resulting ERROR is not mistaken for upgrade damage.
  for tree in /usr/lib/modules/*/; do
    t=$(basename "$tree")
    if [ ! -e "$tree/vmlinuz" ] && [ -d "$tree/build" ]; then
      warn "modules tree '$t' has headers but no kernel image - DKMS will error on it (harmless, but see references/known-quirks.md)"
    fi
  done
else
  ok "dkms not installed"
fi

# --- Existing breakage -------------------------------------------------------
# Recording what was ALREADY broken is the single most useful preflight output:
# after the upgrade it lets you separate "the upgrade did this" from "this was
# already failing", which otherwise costs a lot of wasted diagnosis.
echo "  --- pre-existing failed units (baseline) ---"
systemctl --failed --no-legend 2>/dev/null | sed 's/^[^a-zA-Z]*//' | awk '{print "    "$1}' || true
[ -z "$(systemctl --failed --no-legend 2>/dev/null)" ] && echo "    (none)"

echo "  --- pre-existing .pacnew files (baseline) ---"
find /etc -name '*.pacnew' 2>/dev/null | sed 's/^/    /'
[ -z "$(find /etc -name '*.pacnew' 2>/dev/null)" ] && echo "    (none)"

echo
echo "########## PREFLIGHT: ${BLOCKERS} blocker(s), ${WARNINGS} warning(s) ##########"
[ "$BLOCKERS" -gt 0 ] && exit 1
exit 0
