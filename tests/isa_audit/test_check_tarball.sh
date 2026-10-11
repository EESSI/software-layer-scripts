#!/usr/bin/env bash
# Tests for scripts/isa_audit/check_tarball.sh on small tarballs built here.
# Needs gcc, python3 with iced-x86 and pyelftools; the aarch64 cases also need
# aarch64-linux-gnu-gcc and llvm-objdump, and are skipped without them.
set -uo pipefail
top=$(cd "$(dirname "$0")/../.." && pwd)
check=$top/scripts/isa_audit/check_tarball.sh
refs=$top/scripts/native_flags/references
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fails=0

# build TARGET PACKAGE CC CFLAGS: compile a shared library with one loop into a tarball
build() {
  local target=$1 pkg=$2 cc=$3 cflags=$4
  local dir=$work/root/$target/software/$pkg/1.0/lib
  rm -rf "$work/root" && mkdir -p "$dir"
  printf 'void axpy(int n, double a, const double *x, double *y)\n{ for (int i = 0; i < n; i++) y[i] += a * x[i]; }\n' > "$work/axpy.c"
  # shellcheck disable=SC2086  # CFLAGS holds several flags
  $cc -O3 -shared -fPIC $cflags -o "$dir/libaxpy.so" "$work/axpy.c" || return 1
  tar -C "$work/root" -cf "$work/$pkg.tar" "$target"
}

# expect NAME STATUS PATTERN COMMAND...: run COMMAND, check exit status and output
expect() {
  local name=$1 want=$2 pattern=$3; shift 3
  local out rc
  out=$("$@" 2>&1); rc=$?
  if [ "$rc" -eq "$want" ] && grep -Eq -- "$pattern" <<< "$out"; then
    echo "PASS $name"
  else
    echo "FAIL $name (exit $rc, wanted $want, pattern '$pattern')"; echo "$out" | sed 's/^/    /'
    fails=$((fails + 1))
  fi
}

if python3 -c 'import iced_x86, elftools' 2>/dev/null; then
  allow=$work/allow.txt
  echo 'Axpy avx512f,avx512vl,avx512dq,avx512bw,avx2,avx,fma  # test' > "$allow"
  build x86_64/intel/haswell Axpy gcc '-march=skylake-avx512 -mprefer-vector-width=512'
  expect x86-violation 1 '^ERROR: instruction audit: [1-9][0-9]* violation' \
    "$check" "$work/Axpy.tar" x86_64/intel/haswell "$refs"
  expect x86-violation-feature 1 'VIOLATION\[0\] avx512f ' \
    "$check" "$work/Axpy.tar" x86_64/intel/haswell "$refs"
  expect x86-dispatch-allowed 0 'DISPATCH\[0\] avx512f ' \
    "$check" "$work/Axpy.tar" x86_64/intel/haswell "$refs" "$allow"
  expect x86-same-target 0 'pass against gcc-' \
    "$check" "$work/Axpy.tar" x86_64/intel/skylake_avx512 "$refs"
  build x86_64/intel/haswell Axpy gcc '-march=haswell'
  expect x86-native-pass 0 'pass against gcc-' \
    "$check" "$work/Axpy.tar" x86_64/intel/haswell "$refs"
else
  echo "SKIP x86_64 cases: python3 cannot import iced_x86 and elftools"
fi

if command -v aarch64-linux-gnu-gcc >/dev/null && command -v llvm-objdump >/dev/null; then
  build aarch64/neoverse_n1 Axpy aarch64-linux-gnu-gcc '-march=armv8.2-a+sve'
  expect aarch64-sve-violation 1 '^ERROR: instruction audit: ' \
    "$check" "$work/Axpy.tar" aarch64/neoverse_n1 "$refs"
  expect aarch64-same-target 0 'pass against gcc-' \
    "$check" "$work/Axpy.tar" aarch64/neoverse_v1 "$refs"
else
  echo "SKIP aarch64 cases: aarch64-linux-gnu-gcc or llvm-objdump not found"
fi

build x86_64/intel/haswell Axpy gcc '-march=skylake-avx512'
expect generic-skipped 0 'skipped for x86_64/generic' \
  "$check" "$work/Axpy.tar" x86_64/generic "$refs"
expect no-reference-skipped 0 'no reference for x86_64/intel/nosuchcpu' \
  "$check" "$work/Axpy.tar" x86_64/intel/nosuchcpu "$refs"
expect skip-variable 0 'EESSI_SKIP_ISA_AUDIT is set' \
  env EESSI_SKIP_ISA_AUDIT=1 "$check" "$work/Axpy.tar" x86_64/intel/haswell "$refs"
expect bad-tarball 1 '^ERROR: instruction audit: cannot unpack' \
  "$check" "$work/missing.tar" x86_64/intel/haswell "$refs"

echo "$fails failure(s)"
[ "$fails" -eq 0 ]
