#!/usr/bin/env bash
# Erzeugt ANLEITUNG.pdf aus ANLEITUNG.html (Chrome im Hintergrund, ohne Fenster).
cd "$(dirname "$0")"
google-chrome --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PWD/ANLEITUNG.pdf" "file://$PWD/ANLEITUNG.html" 2>/dev/null
ls -la ANLEITUNG.pdf
