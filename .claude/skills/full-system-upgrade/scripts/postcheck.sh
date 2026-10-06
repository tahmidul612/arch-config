#!/usr/bin/env bash
# Post-upgrade health check.
#
# The point of taking a preflight baseline is realised here: most "failures"
# found after an upgrade were already failing before it. Diffing against the
# baseline is what turns a scary list into a short, honest one - so pass the
# preflight output as $1 whenever you have it.
#
# Usage: postcheck.sh [preflight-baseline.txt]
set -uo pipefail
BASE=${1:-}

echo "########## POST-UPGRADE CHECK ##########"

# --- Reboot needed? ----------------------------------------------------------
# Arch removes the old modules tree on kernel upgrade, so the running kernel can
# no longer load modules it hasn't already loaded. That is the real reason to
# reboot promptly - not superstition.
#
# Kernels are discovered from /usr/lib/modules/*/pkgbase rather than assuming a
# package named `linux`, so flavours like linux-cachyos / linux-cachyos-lts /
# linux-zen are all covered. A tree with no pkgbase is headers-only (no kernel).
run_k=$(uname -r)
echo "  running kernel   : $run_k"
declare -A tree_of=()
for d in /usr/lib/modules/*/; do
  [ -f "$d/pkgbase" ] && tree_of[$(<"$d/pkgbase")]=$(basename "$d")
done
run_pkg=""
for pkg in "${!tree_of[@]}"; do
  [ "${tree_of[$pkg]}" = "$run_k" ] && run_pkg=$pkg
done
if [ -z "$run_pkg" ]; then
  # The running tree is gone (or lost its pkgbase): guess the package from the
  # flavour suffix after pkgver-pkgrel, e.g. 7.2.2-1-cachyos-lts -> linux-cachyos-lts.
  flav=$(echo "$run_k" | sed -E 's/^[0-9.]+(-rc[0-9]+)?-[0-9]+-?//')
  run_pkg=linux${flav:+-$flav}
  case "$run_k" in *-arch*) run_pkg=linux;; esac
fi
inst_v=$(pacman -Q "$run_pkg" 2>/dev/null | awk '{print $2}')
echo "  running package  : $run_pkg (installed ${inst_v:-n/a})"
for pkg in "${!tree_of[@]}"; do
  [ "$pkg" = "$run_pkg" ] || echo "  other kernel     : $pkg ${tree_of[$pkg]}"
done
if [ ! -d "/usr/lib/modules/$run_k" ]; then
  echo "  [REBOOT REQUIRED] modules tree for the running kernel is gone - new hardware/modules will fail until reboot"
elif [ -n "${tree_of[$run_pkg]:-}" ] && [ "${tree_of[$run_pkg]}" != "$run_k" ]; then
  echo "  [REBOOT ADVISED] newer $run_pkg installed (${tree_of[$run_pkg]}) than the one running"
else
  echo "  [ok] kernel current"
fi

# --- Boot artifacts ----------------------------------------------------------
echo "  --- boot files ---"
for pkg in "${!tree_of[@]}"; do
  for f in "/boot/vmlinuz-$pkg" "/boot/initramfs-$pkg.img"; do
    if [ -f "$f" ]; then echo "    [ok] $f ($(stat -c %y "$f" | cut -d. -f1), $(du -h "$f" | cut -f1))"
    else echo "    [MISSING] $f"; fi
  done
done
[ ${#tree_of[@]} -eq 0 ] && echo "    [WARN] no installed kernel package found under /usr/lib/modules"

# --- DKMS --------------------------------------------------------------------
if command -v dkms >/dev/null 2>&1; then
  echo "  --- dkms ---"
  dkms status 2>/dev/null | sed 's/^/    /'
  if [ -n "$(dkms status 2>/dev/null)" ]; then
    for pkg in "${!tree_of[@]}"; do
      n=$(find "/usr/lib/modules/${tree_of[$pkg]}" -path '*/updates/dkms/*' -name '*.ko*' 2>/dev/null | wc -l)
      echo "    $n DKMS module(s) present for $pkg (${tree_of[$pkg]})"
      [ "$n" -eq 0 ] && echo "    [WARN] no DKMS modules built for $pkg - check the analyzer's DKMS lines"
    done
  fi
fi

# --- Failed units, diffed against baseline -----------------------------------
echo "  --- failed systemd units ---"
now_failed=$(systemctl --failed --no-legend 2>/dev/null | sed 's/^[^a-zA-Z]*//' | awk '{print $1}' | sort)
if [ -z "$now_failed" ]; then echo "    [ok] none"; else
  if [ -n "$BASE" ] && [ -f "$BASE" ]; then
    base_failed=$(sed -n '/pre-existing failed units/,/pre-existing .pacnew/p' "$BASE" | grep -oE '[a-zA-Z0-9@._-]+\.(service|socket|timer|mount)' | sort -u)
    newly=$(comm -23 <(echo "$now_failed") <(echo "$base_failed"))
    pre=$(comm -12 <(echo "$now_failed") <(echo "$base_failed"))
    [ -n "$newly" ] && { echo "    [NEW FAILURES - likely caused by this upgrade]"; echo "$newly" | sed 's/^/      /'; }
    [ -n "$pre" ]   && { echo "    [pre-existing, not caused by the upgrade]";     echo "$pre"   | sed 's/^/      /'; }
  else
    echo "$now_failed" | sed 's/^/    /'
    echo "    (no baseline given - cannot tell new failures from pre-existing ones)"
  fi
fi
echo "  --- failed user units ---"
systemctl --user --failed --no-legend 2>/dev/null | sed 's/^[^a-zA-Z]*//' | awk '{print "    "$1}'
[ -z "$(systemctl --user --failed --no-legend 2>/dev/null)" ] && echo "    [ok] none"

# --- pacnew, diffed against baseline ----------------------------------------
echo "  --- .pacnew files ---"
now_pacnew=$(find /etc -name '*.pacnew' 2>/dev/null | sort)
if [ -z "$now_pacnew" ]; then echo "    [ok] none"; else
  if [ -n "$BASE" ] && [ -f "$BASE" ]; then
    base_pacnew=$(grep -oE '/etc/[^ ]*\.pacnew' "$BASE" | sort -u)
    newly=$(comm -23 <(echo "$now_pacnew") <(echo "$base_pacnew"))
    old=$(comm -12 <(echo "$now_pacnew") <(echo "$base_pacnew"))
    [ -n "$newly" ] && { echo "    [NEW from this upgrade - review these]"; echo "$newly" | sed 's/^/      /'; }
    [ -n "$old" ]   && { echo "    [pre-existing backlog]";                 echo "$old"   | sed 's/^/      /'; }
  else
    echo "$now_pacnew" | sed 's/^/    /'
  fi
fi

# --- Runtime linkage ---------------------------------------------------------
# A soname bump that leaves a package linked against a removed library is the
# most common way an upgrade silently breaks one application.
echo "  --- library linkage ---"
if command -v lsof >/dev/null 2>&1; then
  stale=$(sudo lsof -n 2>/dev/null | awk '/DEL.*\.so/ {print $1}' | sort -u)
  n=$(echo "$stale" | grep -c . )
  echo "    $n process name(s) still mapping deleted libraries (normal after an upgrade; a reboot clears them)"
fi
echo "    checking for packages with missing shared libraries..."
# ldd over all of /usr/bin is slow; sample the binaries most likely to matter.
for b in /usr/bin/pacman /usr/bin/paru /usr/bin/systemctl /usr/bin/ssh /usr/bin/python /usr/bin/fish; do
  [ -x "$b" ] || continue
  if ldd "$b" 2>/dev/null | grep -q 'not found'; then
    echo "    [BROKEN] $b:"; ldd "$b" 2>/dev/null | grep 'not found' | sed 's/^/      /'
  fi
done
echo "    [ok] core binaries resolve their libraries"

# --- Orphans -----------------------------------------------------------------
echo "  --- orphaned packages (pacman -Qdtq) ---"
orph=$(pacman -Qdtq 2>/dev/null)
if [ -z "$orph" ]; then echo "    [ok] none"; else
  echo "$orph" | tr '\n' ' ' | fold -sw 150 | sed 's/^/    /'
  echo; echo "    (review before removing - some may be deliberately installed)"
fi

echo
echo "########## POST-UPGRADE CHECK COMPLETE ##########"
