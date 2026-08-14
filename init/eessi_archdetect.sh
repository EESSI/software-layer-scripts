#!/usr/bin/env bash

# Confirm the current shell is Bash >= 4
# (works for sh, bash, dash, zsh, ksh, but not fish, tcsh, elvish)
if [ -n "$BASH_VERSION" ]; then
    # Extract the major version numbers
    bash_version=$(echo "$BASH_VERSION" | grep -oP '^\d+\.\d+')
    major_version=$(echo "$bash_version" | cut -d. -f1)

    # Check if the major version is 4 or higher
    if [ "$major_version" -lt 4 ]; then
        echo "Error: This script must be run with Bash >= 4, you have $BASH_VERSION." >&2
        exit 1
    fi
else
    echo "Error: This script must be run with Bash." >&2
    exit 1
fi

VERSION="1.3.0"

# default log level: only emit warnings or errors
LOG_LEVEL="WARN"
# Default result type is a best match
CPUPATH_RESULT="best"

timestamp () {
    date "+%Y-%m-%d %H:%M:%S"
}

log () {
    # Simple logger function
    declare -A levels=([DEBUG]=0 [INFO]=1 [WARN]=2 [ERROR]=3)
    msg_type="${1:-INFO}"
    msg_body="${2:-'null'}"

    [ ${levels[$msg_type]} ] || log "ERROR" "Unknown log level $msg_type"

    # ignore messages below log level
    [ ${levels[$msg_type]} -lt ${levels[$LOG_LEVEL]} ] && return 0
    # print log message to standard error
    echo "$(timestamp) [$msg_type] $msg_body" >&2
    # exit after any error message
    [ $msg_type == "ERROR" ] && exit 1
}

# Supported CPU specifications
update_arch_specs(){
    # Add contents of given spec file into an array
    # 1: spec file with the additional specs

    [ ! -f "$1" ] && echo "[ERROR] update_arch_specs: spec file not found: $1" >&2 && exit 1
    local spec_file="$1"
    while read spec_line; do
       # format spec line as an array and append it to array with all CPU arch specs
       cpu_arch_spec+=("(${spec_line})")
    # remove comments from spec file
    done < <(sed -E 's/(^|[[:space:]])#.*$//g;/^[[:space:]]*$/d' "$spec_file")
}

# CPU specification of host system
get_cpuinfo(){
    # Return the value from cpuinfo for the matching key
    # 1: string with key pattern

    [ -z "$1" ] && log "ERROR" "get_cpuinfo: missing key pattern in argument list"
    cpuinfo_pattern="^${1}[[:space:]]*:[[:space:]]*"

    # case insensitive match of key pattern and delete key pattern from result
    grep -i "$cpuinfo_pattern" ${EESSI_PROC_CPUINFO:-/proc/cpuinfo} | tail -n 1 | sed "s/$cpuinfo_pattern//i"
}

check_allinfirst(){
    # Return true if all given arguments after the first are found in the first one
    # 1: reference string of space separated values
    # 2,3..: each additional argument is a single value to be found in the reference string

    [ -z "$1" ] && log "ERROR" "check_allinfirst: missing argument with reference string"
    reference="$1"
    shift

    for candidate in "$@"; do
        [[ " $reference " == *" $candidate "* ]] || return 1
    done
    return 0
}

riscv_expand_base(){
    # Expand compact RISC-V base ISA blobs into per-letter tokens at match time.
    # Host /proc/cpuinfo and eessi_arch_riscv.spec both use concatenated forms
    # (e.g. rv64imafdch); without expansion a superset host like rv64imafdcvh
    # fails a subset spec asking for rv64imafdch because the whole blob is one
    # token. Expand both sides so specs stay readable and letter-wise subsets match.
    #   rv64imafdch -> rv64 i m a f d c h
    #   rv64gc      -> rv64 i m a f d c   (g is the IMAFD shorthand)
    # Multi-letter extensions (zicsr, zba, sscofpmf, ...) pass through unchanged.
    local out="" tok letters
    for tok in "$@"; do
        if [ "${tok#rv64}" != "${tok}" ]; then
            # Replace g with imafd before letter-splitting; then split to tokens
            letters=$(printf '%s' "${tok#rv64}" | sed 's/g/imafd/g; s/./& /g')
            out="${out} rv64 ${letters}"
        else
            out="${out} ${tok}"
        fi
    done
    # Normalise whitespace so callers can word-split safely (no empty tokens)
    printf '%s' "${out}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/[[:space:]]\{1,\}/ /g'
}

cpupath(){
    # If EESSI_SOFTWARE_SUBDIR_OVERRIDE is set, use it
    log "DEBUG" "cpupath: Override variable set as '$EESSI_SOFTWARE_SUBDIR_OVERRIDE' "
    [ $EESSI_SOFTWARE_SUBDIR_OVERRIDE ] && echo ${EESSI_SOFTWARE_SUBDIR_OVERRIDE} && exit

    # Identify the best matching CPU architecture from a list of supported specifications for the host CPU
    # Return the path to the installation files in EESSI of the best matching architecture
    local cpu_arch_spec=()

    # Identify the host CPU architecture
    local machine_type=${EESSI_MACHINE_TYPE:-$(uname -m)}
    log "DEBUG" "cpupath: Host CPU architecture identified as '$machine_type'"

    # Populate list of supported specs for this architecture
    case $machine_type in
        "x86_64") local spec_file="eessi_arch_x86.spec";;
        "aarch64") local spec_file="eessi_arch_arm.spec";;
        "ppc64le") local spec_file="eessi_arch_ppc.spec";;
        "riscv64") local spec_file="eessi_arch_riscv.spec";;
        *) log "ERROR" "cpupath: Unsupported CPU architecture $machine_type"
    esac
    # spec files are located in a subfolder with this script
    local base_dir=$(dirname $(readlink -f $0))
    update_arch_specs "$base_dir/arch_specs/${spec_file}"

    # Identify the host CPU vendor
    local cpu_vendor=$(get_cpuinfo "vendor[ _]id")
    if [ "${cpu_vendor}" == "" ]; then
        cpu_vendor=$(get_cpuinfo "cpu[ _]implementer")
        if [ "${cpu_vendor}" == "" ]; then
            cpu_vendor=$(get_cpuinfo "mvendorid")
        fi
    fi
    log "DEBUG" "cpupath: CPU vendor of host system: '$cpu_vendor'"
    # Construct a list of known cpu vendors
    local cpu_vendors=()
    for spec in "${cpu_arch_spec[@]}"; do
        eval "cols=$spec"
        cpu_vendors+=("${cols[1]}")
    done
    log "DEBUG" "cpupath: Known CPU vendors: ${cpu_vendors[*]}"
    # For ARM, if CPU vendor is as-yet-unknown fall back to a default ARM vendor 0x41
    if [ "${machine_type}" == "aarch64" ]; then
        if [[ " ${cpu_vendors[*]} " != *" $cpu_vendor "* ]]; then
            log "DEBUG" "cpupath: Unknown ARM CPU vendor '$cpu_vendor', falling back to '0x41'"
            cpu_vendor="0x41"
        fi
    fi

    # Identify the host CPU flags or features
    # cpuinfo systems print different line identifiers, eg features, instead of flags
    local cpu_flag_tag;
    if [ "${cpu_vendor}" == "ARM" ]; then
        # if CPU vendor field is ARM, then we should be able to determine CPU microarchitecture based on 'flags' field
        cpu_flag_tag='flags'
    # if 64-bit Arm CPU without "ARM" as vendor ID, we need to take into account 'features' field
    elif [ "${machine_type}" == "aarch64" ]; then
        cpu_flag_tag='features'
    # on 64-bit POWER, we need to look at 'cpu' field
    elif [ "${machine_type}" == "ppc64le" ]; then
        cpu_flag_tag='cpu'
    # on 64-bit RISC-V, we need to look at 'isa' field
    elif [ "${machine_type}" == "riscv64" ]; then
        cpu_flag_tag='isa'
    else
        cpu_flag_tag='flags'
    fi

    local cpu_flags=$(get_cpuinfo "$cpu_flag_tag")
    if [ "${machine_type}" == "riscv64" ]; then
        # RISC-V ISA strings use '_' as extension separators.
        # Convert them to space-separated feature tokens so they
        # can be matched like x86 CPU flags, then expand the compact
        # base blob (rv64imafdc...) into per-letter tokens.
        cpu_flags=${cpu_flags//_/ }
        cpu_flags=$(riscv_expand_base ${cpu_flags})
    fi
    log "DEBUG" "cpupath: CPU flags of host system: '$cpu_flags'"

    # Default to generic CPU
    local best_arch_match="$machine_type/generic"
    local all_arch_matches=$best_arch_match

    # Iterate over the supported CPU specifications to find the best match for host CPU
    # Order of the specifications matters, the last one to match will be selected
    for arch in "${cpu_arch_spec[@]}"; do
        eval "arch_spec=$arch"
        # Empty vendor in the spec means "any vendor" (profile paths like riscv64/rva*).
        # Vendor-specific entries still require an exact match (same as before).
        if [ -z "${arch_spec[1]}" ] || [ "${cpu_vendor}x" == "${arch_spec[1]}x" ]; then
            # each flag in this CPU specification must be found in the list of flags of the host
            if [ "${machine_type}" == "riscv64" ]; then
                check_allinfirst "${cpu_flags}" $(riscv_expand_base ${arch_spec[2]}) && best_arch_match=${arch_spec[0]} && \
                    all_arch_matches="$best_arch_match:$all_arch_matches" && \
                    log "DEBUG" "cpupath: host CPU best match updated to $best_arch_match"
            else
                check_allinfirst "${cpu_flags[*]}" ${arch_spec[2]} && best_arch_match=${arch_spec[0]} && \
                    all_arch_matches="$best_arch_match:$all_arch_matches" && \
                    log "DEBUG" "cpupath: host CPU best match updated to $best_arch_match"
            fi
        fi
    done

    if [ "allx" == "${CPUPATH_RESULT}x" ]; then
        log "INFO" "cpupath: all matches for host CPU: $all_arch_matches"
        echo "$all_arch_matches"
    else
        log "INFO" "cpupath: best match for host CPU: $best_arch_match"
        echo "$best_arch_match"
    fi
}

accelpath() {
    # If EESSI_ACCELERATOR_TARGET_OVERRIDE is set, use it
    log "DEBUG" "accelpath: Override variable set as '$EESSI_ACCELERATOR_TARGET_OVERRIDE' "
    if [ ! -z $EESSI_ACCELERATOR_TARGET_OVERRIDE ]; then
        # Regex that allows both NVIDIA and AMD overrides
        if [[ "$EESSI_ACCELERATOR_TARGET_OVERRIDE" =~ ^accel/(nvidia/cc[0-9]+|amd/gfx[0-9a-f]+)$ ]]; then
            echo "$EESSI_ACCELERATOR_TARGET_OVERRIDE"
            return 0
        else
            log "ERROR" "Value of \$EESSI_ACCELERATOR_TARGET_OVERRIDE should match 'accel/nvidia/cc[0-9]+' or 'accel/amd/gfx[0-9a-f]+', but it does not: '$EESSI_ACCELERATOR_TARGET_OVERRIDE'"
            return 1
        fi
    fi

    # check for NVIDIA GPUs via nvidia-smi command
    nvidia_smi=$(command -v nvidia-smi)
    if [[ $? -eq 0 ]]; then
        log "DEBUG" "accelpath: nvidia-smi command found @ ${nvidia_smi}"
        nvidia_smi_out=$(mktemp -p /tmp nvidia_smi_out.XXXXX)
        nvidia-smi --query-gpu=gpu_name,count,driver_version,compute_cap --format=csv,noheader 2>&1 > $nvidia_smi_out
        if [[ $? -eq 0 ]]; then
            nvidia_smi_info=$(head -n 1 $nvidia_smi_out)
            cuda_cc=$(echo $nvidia_smi_info | sed 's/, /,/g' | cut -f4 -d, | sed 's/\.//g')
            log "DEBUG" "accelpath: CUDA compute capability '${cuda_cc}' derived from nvidia-smi output '${nvidia_smi_info}'"
            res="accel/nvidia/cc${cuda_cc}"
            log "DEBUG" "accelpath: result: ${res}"
            echo $res
            rm -f $nvidia_smi_out
        else
            log "DEBUG" "accelpath: nvidia-smi command failed, see output in $nvidia_smi_out"
            exit 3
        fi
    else
        log "DEBUG" "accelpath: nvidia-smi command not found"
        exit 2
    fi
}

# Parse command line arguments
USAGE="Usage: eessi_archdetect.sh [-h][-d][-a] <action: cpupath or accelpath>"

while getopts 'hdva' OPTION; do
    case "$OPTION" in
        h) echo "$USAGE"; exit 0;;
        d) LOG_LEVEL="DEBUG";;
        v) echo "eessi_archdetect.sh v$VERSION"; exit 0;;
        a) CPUPATH_RESULT="all";;
        ?) echo "$USAGE"; exit 1;;
    esac
done
shift "$(($OPTIND -1))"

ARGUMENT=${1:-none}

case "$ARGUMENT" in
    "cpupath") cpupath; exit;;
    "accelpath") accelpath; exit;;
    *) echo "$USAGE"; log "ERROR" "Missing <action> argument (possible actions: 'cpupath', 'accelpath')";;
esac
