#!/bin/bash
# Builds the stripped universal fixture for scenario A into OUT (default ./vault.noindex).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-vault.noindex}
mkdir -p "$out"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos14" -module-name Vault -o "$out/vault-$arch" "$here/Vault.swift"
done
lipo -create "$out/vault-arm64" "$out/vault-x86_64" -output "$out/vault"
strip "$out/vault"
shasum -a 256 "$out/vault"
