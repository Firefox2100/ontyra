#!/usr/bin/env bash
# Install the Ontyra skill for agents that read ~/.agents/skills (copy, so it survives moving the repo).
#   integrations/skills/install.sh [target-dir]    (default ~/.agents/skills)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
target="${1:-$HOME/.agents/skills}"
mkdir -p "$target/ontyra-memory"
cp "$here/ontyra-memory/SKILL.md" "$target/ontyra-memory/SKILL.md"
echo "installed $target/ontyra-memory/SKILL.md"
