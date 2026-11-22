#!/bin/bash
# Build vm-launch and install it for the current user:
#   ~/.local/bin/vm-launch                         release binary
#   ~/.local/bin/vm-display                        symlink to ../looking-glass/vm-display (an existing plain file is
#                                                  kept as vm-display.pre-launcher)
#   ~/.local/share/icons/hicolor/*/apps/           net.local.vm-launch.{svg,png}, PNGs rendered from the SVGs
#   ~/.local/share/applications/                   vm-launch.desktop (<USER> filled in)
# Needs: cargo, webkit2gtk-4.1, rsvg-convert (librsvg).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=$HOME/.local/bin; ICONS=$HOME/.local/share/icons/hicolor; APPS=$HOME/.local/share/applications
mkdir -p "$BIN" "$APPS"

(cd "$HERE" && cargo build --release)
install -m 755 "$HERE/target/release/vm-launch" "$BIN/vm-launch"

VD=$(readlink -f "$HERE/../looking-glass/vm-display")
if [ -e "$BIN/vm-display" ] && [ ! -L "$BIN/vm-display" ]; then
  mv "$BIN/vm-display" "$BIN/vm-display.pre-launcher"
  echo "kept the old vm-display as $BIN/vm-display.pre-launcher"
fi
ln -sfn "$VD" "$BIN/vm-display"

for s in 16 22 24 32 48 64 128 256 512; do
  src=$HERE/icons/net.local.vm-launch.svg; [ "$s" -le 32 ] && src=$HERE/icons/net.local.vm-launch-small.svg
  mkdir -p "$ICONS/${s}x${s}/apps"
  rsvg-convert -w "$s" -h "$s" "$src" -o "$ICONS/${s}x${s}/apps/net.local.vm-launch.png"
done
mkdir -p "$ICONS/scalable/apps"
install -m 644 "$HERE/icons/net.local.vm-launch.svg" "$ICONS/scalable/apps/net.local.vm-launch.svg"

sed "s|/home/<USER>|$HOME|g" "$HERE/vm-launch.desktop" > "$APPS/vm-launch.desktop"
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
echo "installed: vm-launch, vm-display -> $VD, icons, menu entry 'Windows VM'"
