#!/usr/bin/env bash
#
# Build the Printers container image with auto-version increment.
#
# Usage:
#   ./build.sh                          # Build and push to registry (default)
#   ./build.sh --no-push                # Build only, do not push
#   ./build.sh --no-increment           # Skip version increment
#   ./build.sh --image=registry/img:tag # Custom image tag

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

step()  { printf '\n\033[36m=== %s ===\033[0m\n' "$1"; }
error() { printf '\033[31m  ERROR: %s\033[0m\n' "$1" >&2; exit 1; }

PUSH=true
NO_INCREMENT=false
IMAGE="ghcr.io/hemues/printers:latest"

for arg in "$@"; do
    case "$arg" in
        --no-push)       PUSH=false ;;
        --no-increment)  NO_INCREMENT=true ;;
        --image=*)       IMAGE="${arg#--image=}" ;;
        *)               echo "Unknown option: $arg"; exit 1 ;;
    esac
done

step 'Version management'

if ! command -v gh &>/dev/null; then
    error 'GitHub CLI (gh) not found. Install from https://cli.github.com'
fi

SRC_TAG=$(gh release view --repo Hemues/printers-container --json tagName -q '.tagName' 2>/dev/null || echo "v0.0.0")
IMG_TAG=$(gh release view --repo Hemues/printers-images --json tagName -q '.tagName' 2>/dev/null || echo "v0.0.0")

SRC_VER="${SRC_TAG#v}"
IMG_VER="${IMG_TAG#v}"
echo "  Hemues/printers-container latest: $SRC_VER"
echo "  Hemues/printers-images   latest: $IMG_VER"

HIGHEST=$(printf '%s\n%s\n' "$SRC_VER" "$IMG_VER" | sort -V | tail -1)
LOCAL_VER=$(grep -m1 '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo "  Local (pyproject.toml) version: $LOCAL_VER"

EFFECTIVE_HIGHEST=$(printf '%s\n%s\n' "$HIGHEST" "$LOCAL_VER" | sort -V | tail -1)

if [ "$NO_INCREMENT" = false ]; then
    if [ "$EFFECTIVE_HIGHEST" = "$LOCAL_VER" ] && [ "$LOCAL_VER" != "$HIGHEST" ]; then
        NEW_VERSION="$LOCAL_VER"
        echo "  Manual version bump detected — using local version: $NEW_VERSION"
    else
        IFS='.' read -r MAJOR MINOR PATCH <<< "$EFFECTIVE_HIGHEST"
        PATCH=$((PATCH + 1))
        NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
    fi
    sed -i "s/^version = \".*\"/version = \"${NEW_VERSION}\"/" pyproject.toml
    if command -v python3 &>/dev/null && [ -f ui/package.json ]; then
        python3 -c "
import json
with open('ui/package.json', 'r') as f:
    pkg = json.load(f)
pkg['version'] = '${NEW_VERSION}'
with open('ui/package.json', 'w') as f:
    json.dump(pkg, f, indent=2)
    f.write('\n')
"
    fi
    echo "  New version: $NEW_VERSION"
else
    NEW_VERSION="$EFFECTIVE_HIGHEST"
    echo "  Version unchanged (--no-increment)"
fi

step "Building container image"
echo "  Image: $IMAGE"
echo "  Version: $NEW_VERSION"
podman build --no-cache --build-arg VERSION="${NEW_VERSION}" -t "$IMAGE" .

if [ "$PUSH" = true ]; then
    step "Pushing image"
    podman push "$IMAGE"
fi

step 'Creating GitHub release'
gh release create "v${NEW_VERSION}" \
    --repo Hemues/printers-images \
    --title "Printers Container (Build ${NEW_VERSION})" \
    --notes "Container release ${NEW_VERSION}" \
    || echo "  WARN: release create failed (already exists?)"

step 'Build complete'
echo "  Image:   $IMAGE"
echo "  Version: $NEW_VERSION"
