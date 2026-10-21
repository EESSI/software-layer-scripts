#!/bin/bash
#
# Check that the compilers of all toolchains supported in the current EESSI version translate the "native"
# architecture flag (as used by EasyBuild) into *exactly* the same set of target flags on this build host as
# recorded in a reference for the current CPU target (EESSI_SOFTWARE_SUBDIR).
#
# This guards against having multiple build hosts for the same CPU target that (subtly) differ in which
# instruction set extensions the compilers detect, e.g. due to a different CPU stepping/SKU, different kernel
# version (on aarch64, detection relies on hwcaps in /proc/cpuinfo), or CPU features being masked by a hypervisor.
# If such hosts both build for the same CPU target, the result may be binaries that don't run on all
# systems of that CPU target.
#
# Rather than checking the CPU flags reported by lscpu (or similar), we ask the compiler driver itself what it
# resolves the native flag into, since that's what really matters. As this depends on the compiler version,
# references are stored per CPU target, compiler and compiler version, in:
#   <this directory>/references/<EESSI_SOFTWARE_SUBDIR>/<compiler>-<version>.txt
#
# For GCC, all -m* options passed by the driver to cc1 are taken into account (--param options like
# l1-cache-size are not, since they only affect tuning, and are more likely to differ between CPUs of the same type).
# For Clang, all -target-cpu, -tune-cpu, -target-abi and -target-feature options passed to clang -cc1 are taken
# into account.
#
# Requires an initialised EESSI environment (EESSI module loaded) and Lmod's module command.
#
# usage: check_native_flags.sh [--generate]
#
#   --generate   (re)generate the reference files for the CPU target of this host, rather than checking against them.
#
# Environment variables:
#   EESSI_NATIVE_FLAGS_REFERENCE_DIR      - override location of the reference files
#   EESSI_NATIVE_FLAGS_ALLOW_MISSING_REFERENCE - if set, a missing reference file only results in a warning
#                                                (by default, it is treated as an error)

SCRIPT_DIR=$(dirname $(realpath ${BASH_SOURCE[0]}))
TOPDIR=$(realpath ${SCRIPT_DIR}/../..)

source ${TOPDIR}/scripts/utils.sh

REFERENCE_DIR=${EESSI_NATIVE_FLAGS_REFERENCE_DIR:-${SCRIPT_DIR}/references}

generate=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --generate)
            generate=1
            shift
            ;;
        -h|--help)
            sed -n '2,/^$/p' ${BASH_SOURCE[0]} | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            fatal_error "Unknown argument: $1"
            ;;
    esac
done

check_eessi_initialised
if [[ -z "${EESSI_VERSION}" ]]; then
    fatal_error "\$EESSI_VERSION is not set!"
fi
if [[ "$(type -t module)" != "function" ]]; then
    fatal_error "The 'module' command is not available!"
fi

# Native architecture flag as used by EasyBuild (see COMPILER_OPTIMAL_ARCHITECTURE_OPTION
# in easybuild/toolchains/compiler/{gcc,clang,llvm_compilers}.py)
case $(uname -m) in
    x86_64)
        native_flag='-march=native'
        ;;
    aarch64|ppc64le)
        native_flag='-mcpu=native'
        ;;
    *)
        # RISC-V: GCC/Clang don't support a native flag, EasyBuild uses an explicit -march value there
        echo_yellow ">> No native architecture flag known for $(uname -m), skipping check of native compiler flags"
        exit 0
        ;;
esac

# Print the target flags that the GCC driver passes to cc1 when using the native flag, one per line
function gcc_native_flags() {
    local cc=$1
    local cc1_line
    cc1_line=$(${cc} ${native_flag} -### -E - < /dev/null 2>&1 | awk '$1 ~ /\/cc1$/')
    if [[ -z "${cc1_line}" ]]; then
        return 1
    fi
    # Drop quotes, then retain only -m* options (skipping '--param <value>')
    echo "${cc1_line}" | tr -d '"' | tr -s ' ' '\n' | grep '^-m'
}

# Print the target options that the Clang driver passes to clang -cc1 when using the native flag, one per line
function clang_native_flags() {
    local cc=$1
    local cc1_line
    cc1_line=$(${cc} ${native_flag} -### -c -x c - -o /dev/null < /dev/null 2>&1 | grep '"-cc1"')
    if [[ -z "${cc1_line}" ]]; then
        return 1
    fi
    echo "${cc1_line}" | tr -d '"' | tr -s ' ' '\n' \
        | awk '/^-(target-cpu|tune-cpu|target-abi|target-feature)$/ { opt=$0; getline; print opt " " $0 }'
}

# Check whether the given path is part of an installation provided by a loaded module (as opposed to a compiler
# provided by the compat layer, or the host)
function provided_by_module() {
    local path=$1
    local root
    for root in $(env | grep '^EBROOT' | cut -f2 -d=); do
        if [[ -n "${root}" && "${path}" == "$(realpath ${root})/"* ]]; then
            return 0
        fi
    done
    return 1
}

echo ">> Checking native compiler flags for CPU target ${EESSI_SOFTWARE_SUBDIR} (EESSI ${EESSI_VERSION})"
echo ">> Native architecture flag: ${native_flag}"

# use the same file as eb_hooks.py (which can be configured through $EESSI_SUPPORTED_TOOLCHAINS_FILE)
supported_toolchains_file=${EESSI_SUPPORTED_TOOLCHAINS_FILE:-${TOPDIR}/eessi_supported_toolchains.toml}
toolchains=$(python3 ${SCRIPT_DIR}/get_supported_toolchains.py ${supported_toolchains_file} ${EESSI_VERSION})
check_exit_code $? ">> Supported toolchains in EESSI ${EESSI_VERSION}: $(echo ${toolchains})" \
    "Failed to determine supported toolchains for EESSI ${EESSI_VERSION}"

# Determine (unique) set of compilers, by loading each toolchain module (in a subshell)
# Each entry is '<compiler type> <path to compiler>'
declare -A compilers
module_load_out=$(mktemp)
for toolchain in ${toolchains}; do
    if ! module is-avail ${toolchain}; then
        echo_yellow ">> WARNING: module for toolchain ${toolchain} is not available, skipping it"
        continue
    fi
    found=$(
        module load ${toolchain} > ${module_load_out} 2>&1 || exit 1
        for compiler_type in gcc clang; do
            compiler=$(command -v ${compiler_type})
            if [[ -n "${compiler}" ]]; then
                compiler=$(realpath ${compiler})
                if provided_by_module ${compiler}; then
                    echo "${compiler_type} ${compiler}"
                fi
            fi
        done
    )
    if [[ $? -ne 0 ]]; then
        # This happens for toolchains that are deliberately not supported on this CPU target (e.g. foss/2022b on
        # zen4 in EESSI 2023.06): the module then raises an error, which means it can't be used to build either
        echo_yellow ">> WARNING: failed to load module for toolchain ${toolchain}, skipping it. Output of 'module load':"
        sed 's/^/     /' ${module_load_out}
        continue
    fi
    while read compiler_type compiler; do
        if [[ -n "${compiler}" ]]; then
            echo ">> Found ${compiler_type} compiler ${compiler} in toolchain ${toolchain}"
            compilers[${compiler}]=${compiler_type}
        fi
    done <<< "${found}"
done
rm -f ${module_load_out}

if [[ ${#compilers[@]} -eq 0 ]]; then
    echo_yellow ">> WARNING: no compilers found to check native compiler flags for!"
    exit 0
fi

reference_subdir=${REFERENCE_DIR}/${EESSI_SOFTWARE_SUBDIR}
errors=()
missing=()
for compiler in $(echo ${!compilers[@]} | tr ' ' '\n' | sort); do
    compiler_type=${compilers[${compiler}]}
    if [[ ${compiler_type} == "gcc" ]]; then
        version=$(${compiler} -dumpfullversion)
    else
        version=$(${compiler} -dumpversion)
    fi
    if [[ -z "${version}" ]]; then
        fatal_error "Failed to determine version of ${compiler}"
    fi

    flags=$(${compiler_type}_native_flags ${compiler})
    if [[ $? -ne 0 || -z "${flags}" ]]; then
        fatal_error "Failed to determine flags that '${native_flag}' translates to for ${compiler}"
    fi

    reference_file=${reference_subdir}/${compiler_type}-${version}.txt
    if [[ ${generate} -eq 1 ]]; then
        mkdir -p ${reference_subdir}
        echo "${flags}" > ${reference_file}
        echo_green ">> Generated ${reference_file}"
    elif [[ ! -f ${reference_file} ]]; then
        echo_yellow ">> No reference found for ${compiler_type} ${version} (${reference_file})."
        echo_yellow "   '${native_flag}' translates to the following flags for ${compiler} on this host:"
        echo "${flags}" | sed 's/^/     /'
        echo_yellow "To create a reference file, simply copy the above list of flags (without indentation) into the file ${reference_file} and add that to the EESSI/software-layer-scripts repository."
        missing+=("${reference_file}")
    else
        flags_diff=$(diff <(grep -v '^#' ${reference_file}) <(echo "${flags}"))
        if [[ $? -eq 0 ]]; then
            echo_green ">> Flags for ${compiler_type} ${version} match reference (${reference_file}):"
            echo "${flags}" | sed 's/^/     /'
        else
            echo_red ">> Flags for ${compiler_type} ${version} DO NOT MATCH reference (${reference_file})!"
            echo "   Difference ('<' = reference, '>' = this host):"
            echo "${flags_diff}" | sed 's/^/     /'
            errors+=("${compiler_type} ${version}")
        fi
    fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
    msg="No reference found for ${#missing[@]} compiler(s) for CPU target ${EESSI_SOFTWARE_SUBDIR}."
    msg="${msg} These can be created by running '${BASH_SOURCE[0]} --generate' on a build host that is known to be"
    msg="${msg} good, and adding the resulting files to the software-layer-scripts repository."
    if [[ -n "${EESSI_NATIVE_FLAGS_ALLOW_MISSING_REFERENCE}" ]]; then
        echo_yellow ">> WARNING: ${msg}"
    else
        msg="${msg} Set \$EESSI_NATIVE_FLAGS_ALLOW_MISSING_REFERENCE to only print a warning instead."
        fatal_error "${msg}"
    fi
fi

if [[ ${#errors[@]} -gt 0 ]]; then
    msg="Native compiler flags on this build host do not match the reference for CPU target"
    msg="${msg} ${EESSI_SOFTWARE_SUBDIR} for: ${errors[*]}."
    msg="${msg} Building on this host could result in binaries that are not compatible with other systems"
    msg="${msg} of the same CPU target!"
    fatal_error "${msg}"
fi

echo_green ">> Native compiler flags check for CPU target ${EESSI_SOFTWARE_SUBDIR} completed"
