#!/usr/bin/env bash
# Compile the selected NothingOSS GKI tree with its declared Android Clang.
set -euo pipefail

cd "$GITHUB_WORKSPACE"
mkdir -p build-logs
exec > >(tee -a build-logs/nothing-build.log) 2>&1

kernel="$KERNEL_ROOT/common"
toolchain="$GITHUB_WORKSPACE/nothing-clang"
clang_version="$(python -c 'import json; print(json.load(open("nothing-plan.json"))["clang_version"])')"
[[ "$clang_version" =~ ^r[0-9]+[a-z0-9]*$ ]]

if [[ ! -x "$toolchain/bin/clang" ]]; then
  mkdir -p "$toolchain"
  clang_ref="$(python -c 'import json; p=json.load(open("nothing-plan.json")); refs=json.load(open("nothing/toolchains.json")); print(refs.get(p["clang_version"], "refs/heads/" + p["android_version"] + "-release"))')"
  clang_repository=https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86
  clang_sha="$(git ls-remote --exit-code "$clang_repository" "$clang_ref" | cut -f1)"
  [[ "$clang_sha" =~ ^[0-9a-f]{40}$ ]]
  curl -fL --retry 5 --retry-all-errors --connect-timeout 30 \
    "$clang_repository/+archive/$clang_sha/clang-${clang_version}.tar.gz" \
    -o "$RUNNER_TEMP/nothing-clang.tar.gz"
  tar -xzf "$RUNNER_TEMP/nothing-clang.tar.gz" -C "$toolchain"
  printf '%s\n' "$clang_repository@$clang_sha/clang-$clang_version" > "$toolchain/nothing-source.txt"
fi
export PATH="$toolchain/bin:$PATH"
clang --version
export KCFLAGS="${KCFLAGS:-} -D__ANDROID_COMMON_KERNEL__"
export KBUILD_BUILD_USER=NothingBuilder
export KBUILD_BUILD_HOST=github-actions
export KBUILD_BUILD_VERSION=1
custom_time="$(python -c 'import json; print(json.load(open("nothing-plan.json")).get("build_settings", {}).get("build_time", ""))')"
export KBUILD_BUILD_TIMESTAMP="${custom_time:-$(date -u '+%a %b %d %T UTC %Y')}"
echo "Build timestamp: $KBUILD_BUILD_TIMESTAMP"
out="$KERNEL_ROOT/out"
collect_config() {
  local status=$?
  if [[ -f "$out/.config" ]]; then cp "$out/.config" build-logs/kernel.config; fi
  if [[ -f "$out/include/config/kernel.release" ]]; then
    cp "$out/include/config/kernel.release" build-logs/kernel.release
  fi
  exit "$status"
}
trap collect_config EXIT
branch="$(python -c 'import json; p=json.load(open("nothing-plan.json")); print(p["android_version"] + "-" + p["kernel_version"])')"
kmi="$(python -c 'import json; print(json.load(open("nothing-plan.json"))["kmi_generation"])')"
[[ "$branch" =~ ^android[0-9]+-[0-9]+\.[0-9]+$ && "$kmi" =~ ^[0-9]+$ ]]

# Defconfig already contains the inherited upstream patch options. Preserve the
# Nothing KMI identity; the generic workflow's naming step is skipped.
make_args=(-C "$kernel" O="$out" ARCH=arm64 LLVM=1 LLVM_IAS=1 \
  CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_COMPAT=arm-linux-gnueabi- \
  BRANCH="$branch" KMI_GENERATION="$kmi" EXTRAVERSION=)
make "${make_args[@]}" gki_defconfig
local_suffix="$(python -m nothing.versioning prepare --kernel "$kernel" --out "$out")"
make_args+=(LOCALVERSION="$local_suffix")
make "${make_args[@]}" olddefconfig
# Query the kernel itself before starting parallel compilation. The name limit
# applies to the full release, including source config and Android KMI suffixes.
planned_release="$(make "${make_args[@]}" --no-print-directory -s kernelrelease | tail -n 1)"
printf '%s\n' "$planned_release" > build-logs/kernel.release
python -m nothing.versioning check --release "$planned_release"
python -m nothing.verify config --kernel "$kernel" --out "$out"
make "${make_args[@]}" -j"$(nproc)" Image
test -s "$out/arch/arm64/boot/Image"
python -m nothing.verify record --kernel "$kernel" --out "$out" --clang "$toolchain/bin/clang"
