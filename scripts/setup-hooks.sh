#!/bin/sh
# Install git hooks from scripts/ into the local .git/hooks directory.
# Usage: ./scripts/setup-hooks.sh
set -e
HOOKS_DIR="$(git rev-parse --git-dir)/hooks"
ln -sf "$(pwd)/scripts/pre-commit" "$HOOKS_DIR/pre-commit"
echo "pre-commit hook installed."
