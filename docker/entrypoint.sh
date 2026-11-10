#!/bin/sh
# Entrypoint of the kicad-builder image.
#   no arguments:   kicad-fab build /workspace
#   with arguments: run the arguments as a command (for example: bash, make web, kicad-fab check)
set -e

# KiCad writes its settings below $HOME. With "docker run -u UID:GID" the home
# directory is often missing or not writable: use /tmp then.
if [ -z "${HOME:-}" ] || [ "$HOME" = "/" ] || ! [ -w "$HOME" ]; then
    export HOME=/tmp
fi

if [ "$#" -eq 0 ]; then
    exec kicad-fab build /workspace
fi
exec "$@"
