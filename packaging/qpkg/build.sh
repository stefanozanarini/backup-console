#!/usr/bin/env bash
# Costruisce i QPKG dell'agente, uno per architettura QNAP.
#
#   packaging/qpkg/build.sh <versione> <dir-binari> [dir-output]
#
# <dir-binari> deve contenere bc-agent-linux-amd64, bc-agent-linux-arm64 e
# bc-agent-linux-armv7 (come prodotti dalla CI o da `make` in agent/).
# La build gira in un container con il QDK ufficiale (Dockerfile.qdk).
set -euo pipefail

VERSION="${1:?versione mancante}"
BIN_DIR="$(realpath "${2:?directory binari mancante}")"
OUT_DIR="$(realpath -m "${3:-dist/qpkg}")"
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="bc-qdk:latest"
PKG="BackupConsoleAgent"

# Architettura QDK -> binario Go.
declare -A ARCH=(
  [x86_64]=bc-agent-linux-amd64   # Intel/AMD
  [arm_64]=bc-agent-linux-arm64   # ARM 64 bit (AL-324, Cortex-A55...)
  [arm-x41]=bc-agent-linux-armv7  # Annapurna AL-314/AL-212
  [arm-x31]=bc-agent-linux-armv7  # ARMv7 meno recenti
)

for b in "${ARCH[@]}"; do
  [ -f "$BIN_DIR/$b" ] || { echo "manca $BIN_DIR/$b" >&2; exit 1; }
done

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" -f "$HERE/Dockerfile.qdk" "$HERE"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -r "$HERE/$PKG" "$WORK/"
sed -i "s/^QPKG_VER=.*/QPKG_VER=\"$VERSION\"/" "$WORK/$PKG/qpkg.cfg"
for arch in "${!ARCH[@]}"; do
  mkdir -p "$WORK/$PKG/$arch"
  install -m 755 "$BIN_DIR/${ARCH[$arch]}" "$WORK/$PKG/$arch/bc-agent"
done
mkdir -p "$WORK/$PKG/icons" "$WORK/$PKG/config" "$OUT_DIR"
cp "$HERE/icon.svg" "$WORK/icon.svg"

# Come root, così i file nel pacchetto risultano di root:root; poi restituiamo la
# proprietà della directory di lavoro all'utente corrente.
docker run --rm -v "$WORK:/work" -w "/work/$PKG" -e OWNER="$(id -u):$(id -g)" "$IMAGE" bash -euc '
  convert -background none ../icon.svg -resize 64x64 icons/'"$PKG"'.gif
  convert -background none ../icon.svg -resize 80x80 icons/'"$PKG"'_80.gif
  convert -background none ../icon.svg -resize 64x64 -colorspace Gray icons/'"$PKG"'_gray.gif
  chown -R root:root .
  qbuild --exclude ".gitkeep"
  chown -R "$OWNER" /work
'
cp "$WORK/$PKG/build/"*.qpkg "$OUT_DIR/"
( cd "$OUT_DIR" && sha256sum ./*.qpkg > SHA256SUMS )
ls -l "$OUT_DIR"
