#!/bin/sh
# Prints the CHANGELOG.md section for a version, for use as GitHub release notes:
#   gh release create v1.2.3 --notes "$(scripts/release-notes.sh 1.2.3)"
awk -v head="## [$1]" '
  index($0, head) == 1 { on = 1; next }
  on && (index($0, "## [") == 1 || $0 ~ /^\[[^]]+\]: /) { exit }
  on { print }
' "$(dirname "$0")/../CHANGELOG.md" | sed -e '/./,$!d'
