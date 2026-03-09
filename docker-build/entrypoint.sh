#!/bin/sh
set -e

# BASE_PATH support for Flowise
# Patches the UI and server to work behind a reverse proxy with path prefix

BASE_PATH="${BASE_PATH:-}"

if [ -n "$BASE_PATH" ]; then
    echo "Applying BASE_PATH: $BASE_PATH"

    # Remove trailing slash if present
    BASE_PATH=$(echo "$BASE_PATH" | sed 's:/*$::')

    UI_BUILD_PATH="/usr/local/lib/node_modules/flowise/node_modules/flowise-ui/build"
    SERVER_INDEX="/usr/local/lib/node_modules/flowise/dist/index.js"

    # Patch index.html to use BASE_PATH for assets
    if [ -f "$UI_BUILD_PATH/index.html" ]; then
        echo "Patching index.html..."
        # Replace /assets/ with $BASE_PATH/assets/
        sed -i "s|src=\"/assets/|src=\"${BASE_PATH}/assets/|g" "$UI_BUILD_PATH/index.html"
        sed -i "s|href=\"/assets/|href=\"${BASE_PATH}/assets/|g" "$UI_BUILD_PATH/index.html"
        # Replace other root-relative paths
        sed -i "s|href=\"/manifest.json\"|href=\"${BASE_PATH}/manifest.json\"|g" "$UI_BUILD_PATH/index.html"
        sed -i "s|href=\"/favicon|href=\"${BASE_PATH}/favicon|g" "$UI_BUILD_PATH/index.html"
        sed -i "s|href=\"/logo|href=\"${BASE_PATH}/logo|g" "$UI_BUILD_PATH/index.html"
    fi

    # Patch manifest.json
    if [ -f "$UI_BUILD_PATH/manifest.json" ]; then
        echo "Patching manifest.json..."
        sed -i "s|\"src\": \"/|\"src\": \"${BASE_PATH}/|g" "$UI_BUILD_PATH/manifest.json"
        sed -i "s|\"start_url\": \"/\"|\"start_url\": \"${BASE_PATH}/\"|g" "$UI_BUILD_PATH/manifest.json"
    fi

    # Patch the main JS bundle to use BASE_PATH for API calls
    for jsfile in "$UI_BUILD_PATH/assets/"*.js; do
        if [ -f "$jsfile" ]; then
            echo "Patching $(basename "$jsfile")..."
            # Replace API base paths
            sed -i "s|\"/api/v1|\"/flowise/api/v1|g" "$jsfile"
            sed -i "s|'/api/v1|'/flowise/api/v1|g" "$jsfile"
            # Fix socket.io path if present
            sed -i "s|\"/socket.io|\"/flowise/socket.io|g" "$jsfile"
            sed -i "s|'/socket.io|'/flowise/socket.io|g" "$jsfile"
        fi
    done

    # Patch server to mount routes at BASE_PATH
    if [ -f "$SERVER_INDEX" ]; then
        echo "Patching server index.js..."
        # Mount API at BASE_PATH/api/v1 instead of /api/v1
        sed -i "s|this\.app\.use('/api/v1'|this.app.use('${BASE_PATH}/api/v1'|g" "$SERVER_INDEX"
        # Mount static files at BASE_PATH
        sed -i "s|this\.app\.use('/', express_2\.default\.static|this.app.use('${BASE_PATH}', express_2.default.static|g" "$SERVER_INDEX"
        # Mount queue admin at BASE_PATH/admin/queues
        sed -i "s|serverAdapter\.setBasePath('/admin/queues')|serverAdapter.setBasePath('${BASE_PATH}/admin/queues')|g" "$SERVER_INDEX"
        sed -i "s|this\.app\.use('/admin/queues'|this.app.use('${BASE_PATH}/admin/queues'|g" "$SERVER_INDEX"
        # Fix socket.io path
        sed -i "s|path: '/socket.io'|path: '${BASE_PATH}/socket.io'|g" "$SERVER_INDEX"
    fi

    echo "BASE_PATH patching complete"
fi

# Execute the original command
exec "$@"
