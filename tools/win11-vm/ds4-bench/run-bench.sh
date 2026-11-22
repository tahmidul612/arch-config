#!/bin/bash
# Runs the full comparison from the docs: Python (repo ds4gadget.py) vs the Rust port, with the
# shipped 4 ms send loop, with all cores loaded (stress-ng), and with a 1 ms loop. Prints JSON lines.
# Needs: cargo, stress-ng, python-evdev. Takes about 5 minutes.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
cargo build --release --quiet --manifest-path ds4rs/Cargo.toml
RS=$PWD/ds4rs/target/release/ds4rs
was_active=$(systemctl --user is-active ds4-vm 2>/dev/null || true)
[ "$was_active" = active ] && systemctl --user stop ds4-vm      # it would fight over /dev/raw-gadget
trap '[ "$was_active" = active ] && systemctl --user start ds4-vm' EXIT
N=${N:-2000}
python3 -I bench.py py-4ms "$N" python3 -I pywrap.py
python3 -I bench.py rs-4ms "$N" sh -c "exec $RS --pad \$PAD"
stress-ng --cpu "$(nproc)" --timeout 200s -q & S=$!; sleep 2
python3 -I bench.py py-4ms-loaded "$N" python3 -I pywrap.py
python3 -I bench.py rs-4ms-loaded "$N" sh -c "exec $RS --pad \$PAD"
kill $S; wait $S 2>/dev/null || true
SLEEP=0.001 python3 -I bench.py py-1ms "$N" python3 -I pywrap.py
python3 -I bench.py rs-1ms "$N" sh -c "exec $RS --pad \$PAD --sleep-us 1000"
