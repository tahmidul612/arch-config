#!/usr/bin/env bash
# Turn a raw paru transcript into a triaged report.
#
# A 150-package upgrade with AUR builds produces hundreds of lines containing
# "error" or "warning" that mean nothing - autotools chatter, makepkg notices,
# compiler deprecations. Grepping for /error|warning/ and reporting the hits is
# worse than useless: it buries the two or three lines that actually matter.
# So this classifies rather than greps, and prints what it suppressed (with
# counts) so nothing is silently dropped.
#
# Usage: analyze-log.sh <raw.log> [clean-output-path]
set -uo pipefail
RAW=${1:?usage: analyze-log.sh <raw.log> [clean.log]}
CLEAN=${2:-${RAW%.raw.log}.clean.log}

sed 's/\r/\n/g' "$RAW" \
  | sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\x1b\][^\x07]*\x07//g' \
  | tr -d '\000-\010\013\014\016-\037' \
  | grep -vE '^[[:space:]]*$|KiB/s|MiB/s|\[[#-]{20,}\]' > "$CLEAN"

echo "########## LOG ANALYSIS ##########"
echo "  raw:   $RAW"
echo "  clean: $CLEAN  ($(wc -l < "$CLEAN") lines)"
echo

# Build noise: normal output from makepkg/autotools/compilers during AUR builds.
# Each pattern here is something observed to be harmless on a healthy upgrade.
NOISE='WARNING: Using existing \$srcdir|WARNING: Skipping all source|Using existing \$pkgdir|'\
'^autoreconf:|^configure(\.ac)?:[0-9]+: warning:|warning: .* is deprecated in CMake|'\
'readelf: Warning: Gap in build notes|No debugging symbols|Error while writing index for|'\
'^looking for conflicting packages|^:: Calculating (inner )?conflicts|conflicts=\(|'\
'^checking for |-Werror|warning_level|^[[:space:]]*-> (Found|Downloading|Extracting)|'\
'WARNING: Package contains reference to \$srcdir|^==> WARNING: Backup entry'

# --- Tier 1: hard failures ---------------------------------------------------
echo "########## CRITICAL (upgrade did not fully succeed) ##########"
# Non-fatal despite saying ERROR: these come from post-transaction hooks, which
# report failures without rolling back the (already committed) upgrade.
NONFATAL='Missing .* kernel modules tree|Error while writing index for|No debugging symbols'
crit=$(grep -nE '^error:|^==> ERROR:|A failure occurred in|could not satisfy dependencies|'\
'signature from .* is (invalid|unknown trust|marked as expired)|corrupted package|'\
'failed to commit transaction|unable to lock database|No space left on device|'\
'target not found|failed retrieving file' "$CLEAN" | grep -vE "$NOISE" | grep -vE "$NONFATAL")
if [ -n "$crit" ]; then echo "$crit" | sed 's/^/  /'; else echo "  (none)"; fi
echo

# --- Tier 2: succeeded, but something needs a human decision -----------------
echo "########## NEEDS ATTENTION ##########"
att=$(grep -nE 'installed as .*\.pacnew|installed as .*\.pacsave|'\
'^==> WARNING:|directory permissions differ|Failed to start|'\
'requires .* but it is not|Missing .* kernel modules tree|'\
'^warning: (could not|dependency cycle|removing)' "$CLEAN" | grep -vE "$NOISE")
if [ -n "$att" ]; then echo "$att" | sed 's/^/  /'; else echo "  (none)"; fi
echo

# --- Always surface these, they're the load-bearing bits of the transcript ----
echo "########## TRANSACTION SUMMARY ##########"
grep -nE '^Packages \(|^Total Download Size|^Total Installed Size|^Net Upgrade Size|'\
'^:: Running post-transaction hooks|UPGRADE_DONE_[0-9]|^==> Finished making:' "$CLEAN" \
  | cut -c1-200 | sed 's/^/  /'
echo

echo "########## HOOKS THAT RAN ##########"
grep -nE '^\( *[0-9]+/[0-9]+\) [A-Z]' "$CLEAN" | sed 's/^/  /' | head -40
echo

echo "########## SUPPRESSED AS BUILD NOISE ##########"
noise_n=$(grep -cE "$NOISE" "$CLEAN")
echo "  $noise_n lines matched known-benign patterns (see analyze-log.sh NOISE list)"
echo "  to inspect: grep -nE '<pattern>' $CLEAN"
