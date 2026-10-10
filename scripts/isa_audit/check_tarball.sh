#!/usr/bin/env bash
# Audit the instructions in an EESSI build tarball against its CPU target.
#
#   check_tarball.sh TARBALL EESSI_SOFTWARE_SUBDIR REFERENCE_DIR [ALLOW_FILE]
#
# REFERENCE_DIR is the per-target reference directory of
# software-layer-scripts#311 (scripts/native_flags/references); the newest gcc
# reference for the target is used. x86_64 targets are audited with
# isa_audit.py (iced-x86), aarch64 targets with isa_audit_aarch64.py
# (llvm-objdump). Generic targets are skipped. A violation prints an "ERROR:"
# line, which bot/check-build.sh already reports as a failed build.
set -uo pipefail
tarball=$1 subdir=$2 refdir=$3 allow=${4:-}
here=$(dirname "$(readlink -f "$0")")
if [ -n "${EESSI_SKIP_ISA_AUDIT:-}" ]; then
  echo ">> instruction audit: skipped, \$EESSI_SKIP_ISA_AUDIT is set"; exit 0
fi
case $subdir in
  */generic) echo ">> instruction audit: skipped for $subdir"; exit 0 ;;
esac
case ${subdir%%/*} in
  x86_64)
    if ! python3 -c 'import iced_x86, elftools' 2>/dev/null; then
      echo ">> instruction audit: skipped, python3 cannot import iced_x86 and elftools (pip install iced-x86 pyelftools)"
      exit 0
    fi ;;
  aarch64)
    if ! command -v llvm-objdump >/dev/null; then
      echo ">> instruction audit: skipped, llvm-objdump (LLVM 20 or later) is not on \$PATH"
      exit 0
    fi ;;
esac
ref=$(ls "$refdir/$subdir"/gcc-*.txt 2>/dev/null | sort -V | tail -1)
if [ -z "$ref" ]; then
  [ "${subdir%%/*}" = aarch64 ] && ref=$(ls "$refdir/$subdir"/clang-*.txt 2>/dev/null | sort -V | tail -1)
fi
if [ -z "$ref" ]; then
  echo ">> instruction audit: no reference for $subdir under $refdir; skipped"
  exit 0
fi
work=$(mktemp -d)
# installations are read-only (EASYBUILD_READ_ONLY_INSTALLDIR), so make them writable before removing them
trap 'chmod -R u+w "$work" 2>/dev/null; rm -rf "$work"' EXIT
tar -xf "$tarball" -C "$work" || { echo "ERROR: instruction audit: cannot unpack $tarball"; exit 1; }
case ${subdir%%/*} in
  x86_64)  out=$(python3 "$here/isa_audit.py" ${allow:+--allow "$allow"} "$ref" "$work") ;;
  aarch64) out=$(python3 "$here/isa_audit_aarch64.py" "$ref" "$work") ;;
  *) echo ">> instruction audit: no auditor for ${subdir%%/*}"; exit 0 ;;
esac
rc=$?
echo "$out" | grep -E "^(audited|DISPATCH|VIOLATION|RESULT)" | sed 's/^/>> instruction audit: /'
if [ $rc -ne 0 ]; then
  echo "ERROR: instruction audit: $(echo "$out" | grep -c '^VIOLATION') violation(s) (feature, file) beyond $subdir ($(basename "$ref"))"
  exit 1
fi
echo ">> instruction audit: pass against $(basename "$ref")"
