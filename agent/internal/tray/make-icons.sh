#!/usr/bin/env bash
# Rigenera icon.ico e icon-gray.ico da assets/logo-ags.png (serve ImageMagick 6).
#   16-24 px: solo il simbolo rosso (a quelle dimensioni la scritta non si legge)
#   32-128 px: logo completo
#   icon-gray.ico: desaturata e schiarita, per "non connesso"
set -euo pipefail
cd "$(dirname "$0")"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
src=assets/logo-ags.png

convert "$src" -background black -gravity center -extent 106x106 "$work/full.png"
bbox=$(convert "$src" -fx "(r-g>0.3 && r-b>0.3)?1:0" -trim -format "%wx%h%X%Y" info:)
convert "$src" -crop "$bbox" +repage "$work/swirl_raw.png"
read -r w h < <(identify -format "%w %h\n" "$work/swirl_raw.png")
m=$(( (w > h ? w : h) * 9 / 8 ))
convert "$work/swirl_raw.png" -background black -gravity center -extent "${m}x${m}" "$work/swirl.png"

color=() gray=()
for s in 16 20 24; do convert "$work/swirl.png" -resize "${s}x${s}" -modulate 115,120 "$work/c$s.png"; color+=("$work/c$s.png"); done
for s in 32 48 64 128; do convert "$work/full.png" -resize "${s}x${s}" "$work/c$s.png"; color+=("$work/c$s.png"); done
for f in "${color[@]}"; do g="${f%.png}-g.png"; convert "$f" -modulate 100,0 -level 0%,45% "$g"; gray+=("$g"); done
convert "${color[@]}" icon.ico
convert "${gray[@]}" icon-gray.ico
echo "icon.ico e icon-gray.ico rigenerate"
