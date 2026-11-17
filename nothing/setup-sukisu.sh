#!/usr/bin/env bash
set -euo pipefail

ref="${1:?Expected a SukiSU branch, tag or commit}"
repository="${SUKISU_REPOSITORY:?Missing self-hosted SukiSU repository}"
git clone --branch main -- "$repository" KernelSU

case "$ref" in
  main|builtin) ref="refs/remotes/origin/$ref" ;;
esac
commit="$(git -C KernelSU rev-parse --verify --end-of-options "$ref^{commit}")"
git -C KernelSU show refs/remotes/origin/main:kernel/setup.sh | bash -s -- "$commit"

if [[ "$(git -C KernelSU remote get-url origin)" != "$repository" ||
      "$(git -C KernelSU rev-parse HEAD)" != "$commit" ]]; then
  echo "::error::SukiSU installer did not retain the requested repository and commit" >&2
  exit 1
fi
echo "SukiSU source: $repository@$commit"
