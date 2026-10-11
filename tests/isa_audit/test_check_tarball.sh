#!/usr/bin/env bash
# Tests for scripts/isa_audit/check_tarball.sh on small tarballs built here,
# for native targets and for the generic baselines.
# Needs gcc, python3 with iced-x86 and pyelftools; the aarch64 cases also need
# aarch64-linux-gnu-gcc and llvm-objdump, and are skipped without them.
set -uo pipefail
top=$(cd "$(dirname "$0")/../.." && pwd)
check=$top/scripts/isa_audit/check_tarball.sh
refs=$top/scripts/native_flags/references
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fails=0

# build TARGET PACKAGE CC CFLAGS [EXTRA]: compile a shared library with one loop,
# one atomic increment and the C code in EXTRA into a tarball laid out the way
# create_tarball.sh writes it
build() {
  local target=$1 pkg=$2 cc=$3 cflags=$4 extra=${5:-}
  local dir=$work/root/$target/software/$pkg/1.0/lib
  rm -rf "$work/root" && mkdir -p "$dir"
  cat > "$work/axpy.c" <<'SRC'
#include <stdatomic.h>
_Atomic long calls;
void axpy(int n, double a, const double *x, double *y)
{ atomic_fetch_add(&calls, 1); for (int i = 0; i < n; i++) y[i] += a * x[i]; }
/* GCC writes count-trailing-zeros as rep bsf, which decodes as TZCNT */
int lowest_bit(unsigned v) { return __builtin_ctz(v | 1u); }
SRC
  echo "$extra" >> "$work/axpy.c"
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
  expect x86-generic-violation 1 'VIOLATION\[0\] avx2 ' \
    "$check" "$work/Axpy.tar" x86_64/generic "$refs"
  build x86_64/generic Axpy gcc '-march=x86-64 -mtune=generic'
  expect x86-generic-pass 0 'pass against baseline.txt' \
    "$check" "$work/Axpy.tar" x86_64/generic "$refs"
  # an AVX2 kernel next to baseline code, as a run-time dispatcher would pick it
  build x86_64/generic Axpy gcc '-march=x86-64 -mtune=generic' \
    '__attribute__((target("avx2"))) void axpy_avx2(int n, double a, const double *x, double *y)
{ for (int i = 0; i < n; i++) y[i] += a * x[i]; }'
  echo 'Axpy avx,avx2 axpy_avx2  # test' > "$work/allow-fn.txt"
  expect x86-generic-function-dispatch 0 'DISPATCH\[0\] avx2 ' \
    "$check" "$work/Axpy.tar" x86_64/generic "$refs" "$work/allow-fn.txt"
  echo 'Axpy avx,avx2 other_*  # test' > "$work/allow-fn.txt"
  expect x86-generic-function-miss 1 'VIOLATION\[0\] avx2 ' \
    "$check" "$work/Axpy.tar" x86_64/generic "$refs" "$work/allow-fn.txt"
else
  echo "SKIP x86_64 cases: python3 cannot import iced_x86 and elftools"
fi

if command -v aarch64-linux-gnu-gcc >/dev/null && command -v llvm-objdump >/dev/null; then
  build aarch64/neoverse_n1 Axpy aarch64-linux-gnu-gcc '-march=armv8.2-a+sve'
  expect aarch64-sve-violation 1 '^ERROR: instruction audit: ' \
    "$check" "$work/Axpy.tar" aarch64/neoverse_n1 "$refs"
  expect aarch64-same-target 0 'pass against gcc-' \
    "$check" "$work/Axpy.tar" aarch64/neoverse_v1 "$refs"
  expect aarch64-generic-sve-violation 1 'VIOLATION sve ' \
    "$check" "$work/Axpy.tar" aarch64/generic "$refs"
  # GCC's default -moutline-atomics puts LSE in libgcc helpers chosen by HWCAP_ATOMICS
  build aarch64/generic Axpy aarch64-linux-gnu-gcc '-mcpu=generic -moutline-atomics'
  expect aarch64-generic-pass 0 'pass against baseline.txt' \
    "$check" "$work/Axpy.tar" aarch64/generic "$refs"
  # the same helpers in a stripped library, found by their HWCAP check
  build aarch64/generic Axpy aarch64-linux-gnu-gcc '-mcpu=generic -moutline-atomics -s'
  expect aarch64-generic-stripped-pass 0 'pass against baseline.txt' \
    "$check" "$work/Axpy.tar" aarch64/generic "$refs"
  build aarch64/generic Axpy aarch64-linux-gnu-gcc '-march=armv8.1-a -mno-outline-atomics'
  expect aarch64-generic-lse-violation 1 'VIOLATION lse ' \
    "$check" "$work/Axpy.tar" aarch64/generic "$refs"
  echo 'Axpy lse  # test' > "$work/allow-a64.txt"
  expect aarch64-generic-lse-dispatch 0 'DISPATCH lse ' \
    "$check" "$work/Axpy.tar" aarch64/generic "$refs" "$work/allow-a64.txt"
  echo 'Axpy lse axpy  # test' > "$work/allow-a64.txt"
  expect aarch64-generic-function-dispatch 0 'DISPATCH lse ' \
    "$check" "$work/Axpy.tar" aarch64/generic "$refs" "$work/allow-a64.txt"
  echo 'Axpy lse other_*  # test' > "$work/allow-a64.txt"
  expect aarch64-generic-function-miss 1 'VIOLATION lse ' \
    "$check" "$work/Axpy.tar" aarch64/generic "$refs" "$work/allow-a64.txt"
else
  echo "SKIP aarch64 cases: aarch64-linux-gnu-gcc or llvm-objdump not found"
fi

build x86_64/intel/haswell Axpy gcc '-march=skylake-avx512'
expect no-reference-skipped 0 'no reference for x86_64/intel/nosuchcpu' \
  "$check" "$work/Axpy.tar" x86_64/intel/nosuchcpu "$refs"
expect skip-variable 0 'EESSI_SKIP_ISA_AUDIT is set' \
  env EESSI_SKIP_ISA_AUDIT=1 "$check" "$work/Axpy.tar" x86_64/intel/haswell "$refs"
expect bad-tarball 1 '^ERROR: instruction audit: cannot unpack' \
  "$check" "$work/missing.tar" x86_64/intel/haswell "$refs"

echo "$fails failure(s)"
[ "$fails" -eq 0 ]
