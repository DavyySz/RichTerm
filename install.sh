#!/usr/bin/env bash
# Alles einrichten, ohne danach zu starten. (Beim ersten `./richterm` passiert dasselbe automatisch.)
exec "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/setup.sh" --full "$@"
