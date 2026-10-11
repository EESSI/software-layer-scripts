#!/usr/bin/env bash
# Write the baseline references that check_tarball.sh uses for the generic CPU targets.
#
#   generate_generic_references.sh [GCC]
#
# EasyBuild builds generic targets with --optarch=GENERIC, which GCC turns into
# '-march=x86-64 -mtune=generic' on x86_64 and '-mcpu=generic' (armv8-a) on aarch64.
# For x86_64, GCC is asked which extensions that option enables (gcc -Q --help=target),
# for every extension named in the newest x86_64 native-flags reference of the same GCC
# version, and the result is written in the same -mX / -mno-X form as those references.
# For aarch64, the reference is the single -mcpu line, which isa_audit_aarch64.py reads.
set -euo pipefail
gcc=${1:-gcc}
here=$(dirname "$(readlink -f "$0")")
refs=$here/../native_flags/references
out=$here/references
version=$("$gcc" -dumpfullversion)
native=$(ls "$refs"/x86_64/*/*/gcc-"$version".txt | head -1)
[ -n "$native" ] || { echo "no x86_64 native reference for GCC $version under $refs" >&2; exit 1; }
targets=$("$gcc" -march=x86-64 -mtune=generic -Q --help=target)
mkdir -p "$out/x86_64/generic" "$out/aarch64/generic"
{
  echo "-march=x86-64"
  grep -E '^-m(no-)?[a-z0-9.-]+$' "$native" | grep -vE '^-m(arch|tune)=' | sed -E 's/^-m(no-)?//' | while read -r ext; do
    state=$(awk -v opt="-m$ext" '$1 == opt {print $2}' <<< "$targets")
    case $state in
      "[enabled]") echo "-m$ext" ;;
      "[disabled]") echo "-mno-$ext" ;;
      *) echo "GCC $version reports no state for -m$ext" >&2; exit 1 ;;
    esac
  done
  echo "-mtune=generic"
} > "$out/x86_64/generic/baseline.txt"
printf -- '-mcpu=generic\n' > "$out/aarch64/generic/baseline.txt"
echo "wrote $out/x86_64/generic/baseline.txt (GCC $version, extension list from ${native#"$refs"/})"
echo "wrote $out/aarch64/generic/baseline.txt"
