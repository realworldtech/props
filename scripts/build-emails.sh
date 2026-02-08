#!/usr/bin/env bash
# Compile MJML email templates to HTML using npx.
# Requires Node.js / npm to be installed.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MJML_DIR="${PROJECT_ROOT}/src/templates/emails/mjml"
OUTPUT_DIR="${PROJECT_ROOT}/src/templates/emails"

# Check for npx
if ! command -v npx &> /dev/null; then
    echo "Error: npx not found. Please install Node.js (https://nodejs.org/)." >&2
    exit 1
fi

# Compile each non-underscore .mjml file
count=0
for src in "${MJML_DIR}"/[a-z]*.mjml; do
    [ -f "$src" ] || continue
    name="$(basename "${src%.mjml}")"
    dest="${OUTPUT_DIR}/${name}.html"
    echo "Compiling ${name}.mjml -> ${name}.html"
    npx mjml "$src" -o "$dest"
    count=$((count + 1))
done

echo "Done: compiled ${count} email template(s)."
