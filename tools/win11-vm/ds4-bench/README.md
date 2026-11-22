# ds4-bench

Latency benchmark for the virtual DualShock 4 (`../ds4gadget.py`), and a Rust port of the gadget used to check whether a rewrite would lower it. The write-up is in [`docs/Gaming/Windows VM/input.md`](../../../docs/Gaming/Windows%20VM/input.md#latency-python-vs-rust).

## Files

| File | Installs to | Setup | Status |
| --- | --- | --- | --- |
| `run-bench.sh` | run from here | `./run-bench.sh` (`N=200 ./run-bench.sh` for a quick run); stops and restarts `ds4-vm.service` | Benchmark |
| `bench.py` | run from here | `./bench.py NAME N CMD...`, one variant | Benchmark |
| `pywrap.py` | run from here | started by `bench.py`; runs `../ds4gadget.py` on the fake pad, `SLEEP=` changes the send-loop sleep | Benchmark |
| `ds4rs/` | built by `run-bench.sh` (`target/` is not committed) | `ds4rs --pad /dev/input/eventN [--sleep-us N]` | Benchmark only, not a replacement |
| `results-2026-11-21.jsonl` | | output of the run in the docs (N=2000, 20 cores) | Reference |

Needs `python-evdev`, `cargo`, `stress-ng`, write access to `/dev/uinput` (group `input`), `/dev/raw-gadget`, and the `dummy_hcd`, `raw_gadget` and `hid_playstation` modules. A real Xbox pad can stay plugged in: both gadgets read only the fake pad the benchmark creates.
