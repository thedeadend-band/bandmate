#!/usr/bin/env bash
# Render/install only. Enabling services is a separate operator action.
set -euo pipefail
APP_DIR="${BANDMATE_APP_DIR:-/srv/bandmate}"
OUTPUT_DIR="${BANDMATE_UNIT_DIR:-/etc/systemd/system}"
SERVICE_USER="${BANDMATE_SERVICE_USER:?Set BANDMATE_SERVICE_USER to the verified unprivileged runtime user}"
[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ && "$SERVICE_USER" != root ]] || { echo 'Invalid unprivileged service user' >&2; exit 1; }
[[ "$APP_DIR" =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'Invalid application path' >&2; exit 1; }
id "$SERVICE_USER" >/dev/null
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$OUTPUT_DIR"
for source in "$SCRIPT_DIR"/systemd/*; do
    target="$OUTPUT_DIR/$(basename "$source")"
    # Never overwrite an operator-customized unit silently.
    rendered="$(sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" "$source")"
    if [[ -f "$target" ]] && [[ "$(cat "$target")" != "$rendered" ]]; then
        echo "Refusing to overwrite customized unit: $target" >&2
        exit 1
    fi
done
for source in "$SCRIPT_DIR"/systemd/*; do
    sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" "$source" > "$OUTPUT_DIR/$(basename "$source")"
    chmod 644 "$OUTPUT_DIR/$(basename "$source")"
done
echo "Feature units written to $OUTPUT_DIR; no services started or enabled."
