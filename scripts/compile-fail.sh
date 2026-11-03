#!/usr/bin/env bash
# Each directory in compile_fail/ is a tiny ingot that depends on `sealed`
# and makes one classic auction mistake. Every one of them must be rejected
# by `fe check`, with the error named on its `// expect:` line.
set -uo pipefail
cd "$(dirname "$0")/.."
status=0
for dir in compile_fail/*/; do
  name=$(basename "$dir")
  expect=$(sed -n 's|^// expect: ||p' "$dir/src/lib.fe")
  if output=$(fe check --color never "$dir" 2>&1); then
    echo "FAIL  $name: compiled, but it should not"
    status=1
  elif grep -qF -- "$expect" <<<"$output"; then
    echo "PASS  $name: rejected ($expect)"
  else
    echo "FAIL  $name: rejected, but not with: $expect"
    echo "$output"
    status=1
  fi
done
exit $status
