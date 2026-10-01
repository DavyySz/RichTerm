#!/usr/bin/env bash
# RichTerm starten. bin/ kommt in den PATH, damit `rt` überall im Terminal verfügbar ist.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$DIR/bin:$PATH"
exec python3 "$DIR/richterm.py" "$@"
