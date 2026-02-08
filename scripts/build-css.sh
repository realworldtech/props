#!/usr/bin/env bash
# Build Tailwind CSS using the standalone CLI (v4).
# Downloads the binary automatically if not present.
set -euo pipefail

TAILWIND_VERSION="v4.1.18"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${PROJECT_ROOT}/bin"
CLI="${BIN_DIR}/tailwindcss"
INPUT="${PROJECT_ROOT}/src/static/css/input.css"
OUTPUT="${PROJECT_ROOT}/src/static/css/tailwind.css"

# Detect platform
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "${OS}" in
    linux)  PLATFORM="linux" ;;
    darwin) PLATFORM="macos" ;;
    *)      echo "Unsupported OS: ${OS}" >&2; exit 1 ;;
esac
case "${ARCH}" in
    x86_64|amd64) ARCH_SUFFIX="x64" ;;
    arm64|aarch64) ARCH_SUFFIX="arm64" ;;
    *)             echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;;
esac

# Download CLI if missing
if [ ! -x "${CLI}" ]; then
    echo "Downloading Tailwind CSS ${TAILWIND_VERSION} (${PLATFORM}-${ARCH_SUFFIX})..."
    mkdir -p "${BIN_DIR}"
    curl -sLo "${CLI}" \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-${PLATFORM}-${ARCH_SUFFIX}"
    chmod +x "${CLI}"
    echo "Downloaded to ${CLI}"
fi

# Build
MINIFY_FLAG=""
if [ "${1:-}" = "--minify" ] || [ "${1:-}" = "-m" ]; then
    MINIFY_FLAG="--minify"
fi

echo "Building CSS..."
"${CLI}" -i "${INPUT}" -o "${OUTPUT}" ${MINIFY_FLAG}
echo "Done: ${OUTPUT} ($(wc -c < "${OUTPUT}" | tr -d ' ') bytes)"
