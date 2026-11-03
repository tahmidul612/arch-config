# Mosh

[Mosh](https://mosh.org/) (mobile shell) is a remote terminal application that replaces SSH for interactive sessions. It stays connected through network changes, roaming, and intermittent connectivity, and provides local echo for low-latency typing.

## Installation

```bash
sudo pacman -S mosh
```

Mosh also requires the server binary on the remote host:

```bash
# On the remote Arch/CachyOS machine
sudo pacman -S mosh
```

## Firewall

Mosh uses UDP ports **60000–61000**. Open them on the remote host:

=== "ufw"

    ```bash
    sudo ufw allow 60000:61000/udp
    ```

=== "firewalld"

    ```bash
    sudo firewall-cmd --permanent --add-port=60000-61000/udp
    sudo firewall-cmd --reload
    ```

=== "nftables"

    ```bash
    # Add to your nftables ruleset
    sudo nft add rule inet filter input udp dport 60000-61000 accept
    ```

## Usage

```bash
# Basic connection (uses SSH for key exchange, then switches to UDP)
mosh user@hostname

# Use a specific SSH key
mosh --ssh="ssh -i ~/.ssh/id_ed25519" user@hostname

# Specify a remote port for SSH
mosh --ssh="ssh -p 2222" user@hostname
```

Mosh will automatically use your `~/.ssh/config` aliases, so existing SSH host aliases like `mosh ollivanders` work out of the box.

## Troubleshooting

### Missing shared library (`libabsl_*.so`)

```
mosh-client: error while loading shared libraries: libabsl_log_internal_check_op.so.2605.0.0: cannot open shared object file: No such file or directory
```

**Cause:** Mosh links against [Abseil](https://abseil.io/) (`abseil-cpp`), whose `.so` version encodes the release date (e.g. `2605` = May 2026). If mosh was built against a newer `abseil-cpp` than what is installed, the library is missing.

**Fix:** Update `abseil-cpp` (and all packages) with a full system upgrade:

```bash
sudo pacman -Syu
```

Partial upgrades on Arch/CachyOS are unsupported and cause exactly this kind of ABI mismatch.

### Server not found

If you get `bash: mosh-server: command not found` on the remote, install mosh there too:

```bash
sudo pacman -S mosh   # Arch/CachyOS
sudo apt install mosh # Debian/Ubuntu
sudo dnf install mosh # Fedora
```
