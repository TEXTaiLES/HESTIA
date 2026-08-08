#!/bin/sh
# Rebuild the Directus extension from src/ before Directus boots.
#
# The compiled bundle (index.js) is gitignored, so git never updates it on
# checkout and it outlives the source it was built from: a branch without a
# feature would still serve that feature's routes and UI. Directus 9 hardcodes
# `entrypoint: index.js` for local extensions and reads only that bundle.
#
# Run from the directus service's `command:` so it also fires on
# `docker compose restart directus`, which does not re-evaluate depends_on.
# Never exits non-zero: a broken build costs the portal, not the admin UI.

EXT="${EXT:-/directus/extensions/endpoints/archive}"
log() { echo "[ext] $*"; }

cd "$EXT" 2>/dev/null || { log "no extension at $EXT"; exit 0; }

# Line 1 of .build-stamp is the source hash, line 2 the lockfile hash.
stamp="$({ md5sum package.json; find src -type f -exec md5sum {} + | sort; } 2>/dev/null | md5sum | cut -d' ' -f1)"
lock="$(md5sum package-lock.json 2>/dev/null | cut -d' ' -f1)"

if [ "$EXTENSIONS_FORCE_REBUILD" != "true" ] && [ -f index.js ] &&
   [ "$stamp" = "$(sed -n 1p .build-stamp 2>/dev/null)" ]; then
    log "up to date, skipping"
    exit 0
fi

# Delete before anything can fail: no error path may leave the previous
# branch's bundle in place. `dist` is legacy from when the SDK wrote there.
# .build-stamp is deliberately kept — the skip above also requires index.js,
# which is now gone, so a stale stamp cannot cause a missed rebuild, and
# keeping line 2 avoids a needless reinstall after a failed build.
rm -rf index.js dist

if [ ! -d node_modules/@directus/extensions-sdk ] ||
   [ "$lock" != "$(sed -n 2p .build-stamp 2>/dev/null)" ]; then
    log "installing dependencies (this takes a few minutes)"
    npm ci --no-audit --no-fund --loglevel=error || { log "DEPENDENCY INSTALL FAILED"; exit 0; }
fi

log "building"
if node node_modules/@directus/extensions-sdk/cli.js build; then
    printf '%s\n%s\n' "$stamp" "$lock" > .build-stamp
    log "rebuilt"
else
    log "BUILD FAILED - starting without the extension; fix src/ and restart"
fi
exit 0
