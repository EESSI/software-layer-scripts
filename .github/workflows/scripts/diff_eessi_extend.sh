#!/bin/bash
set -euo pipefail

shopt -s nullglob

total_failures=0
failure_summary=()

compare_group() {
    local version="$1"
    shift
    local files=("$@")

    (( ${#files[@]} > 1 )) || return 0

    local ref="${files[0]}"
    local failures=0

    # echo
    # echo "Reference file: $ref"

    for f in "${files[@]:1}"; do
        # echo -e "\tComparing with $f"

        if ! diff \
            <(grep -v '^local root = ' "$ref") \
            <(grep -v '^local root = ' "$f"); then

            failure_summary+=("$version|$ref|$f")

            ((++total_failures))
        fi
    done
}

for version_dir in /cvmfs/software.eessi.io/versions/*; do
    version=$(basename "$version_dir")

    x86_64_files=(
        "$version_dir"/software/linux/x86_64/*/modules/all/EESSI-extend/"$version"-easybuild.lua
        "$version_dir"/software/linux/x86_64/*/*/modules/all/EESSI-extend/"$version"-easybuild.lua
    )
    compare_group "$version" "${x86_64_files[@]}"

    aarch64_files=(
        "$version_dir"/software/linux/aarch64/*/modules/all/EESSI-extend/"$version"-easybuild.lua
        "$version_dir"/software/linux/aarch64/*/*/modules/all/EESSI-extend/"$version"-easybuild.lua
    )
    # For aarch64 we also check our RISC-V development repo
    riscv_version_dirs=(/cvmfs/dev.eessi.io/riscv/versions/"$version"-*)
    if ((${#riscv_version_dirs[@]})); then
        latest_riscv_version_dir="${riscv_version_dirs[-1]}"
        aarch64_files+=(
            "$latest_riscv_version_dir"/software/linux/riscv64/*/modules/all/EESSI-extend/"$version"-easybuild.lua
            "$latest_riscv_version_dir"/software/linux/riscv64/*/*/modules/all/EESSI-extend/"$version"-easybuild.lua
        )
    fi

    compare_group "$version" "${aarch64_files[@]}"
done

if (( total_failures > 0 )); then
    echo
    echo "Summary of differences:"
    for entry in "${failure_summary[@]}"; do
        IFS='|' read -r version ref file <<< "$entry"
        printf 'Version:   %s\n' "$version"
        printf 'Reference: %s\n' "$ref"
        printf 'File:      %s\n\n' "$file"
    done

    echo "$total_failures file(s) differed."
    exit 1
fi

echo "All files matched."
