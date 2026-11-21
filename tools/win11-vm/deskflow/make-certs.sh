#!/usr/bin/env bash
# Make one TLS identity for the host and one for the guest, each pinning the other's fingerprint,
# so neither side shows a trust prompt. Output: ./host/ (copy to ~/.config/Deskflow/tls/)
# and ./guest/ (copy to C:\ProgramData\Deskflow\tls\).
# The minimal -config file is there because CachyOS ships no /etc/ssl/openssl.cnf.
set -euo pipefail
umask 077
cnf=$(mktemp)
trap 'rm -f "$cnf"' EXIT
printf '[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=Deskflow\n' > "$cnf"
gen() {
  mkdir -p "$1"
  openssl req -config "$cnf" -x509 -newkey rsa:2048 -noenc -keyout "$1/key" -out "$1/crt" -days 3650
  cat "$1/key" "$1/crt" > "$1/deskflow.pem"; rm -f "$1/key" "$1/crt"
}
fp() { openssl x509 -in "$1" -outform DER | sha256sum | cut -d' ' -f1; }
gen host; gen guest
echo "v2:sha256:$(fp guest/deskflow.pem)" > host/trusted-clients
echo "v2:sha256:$(fp host/deskflow.pem)" > guest/trusted-servers
ls -l host guest
