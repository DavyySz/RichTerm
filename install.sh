#!/usr/bin/env bash
# Alles einrichten, ohne danach zu starten. (Beim ersten `./richterm` passiert dasselbe automatisch.)
exec "$(dirname "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")")/setup.sh" --full "$@"
