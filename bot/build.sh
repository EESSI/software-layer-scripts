#!/usr/bin/env bash
#
# script to build the EESSI software layer. Intended use is that it is called
# by a (batch) job running on a compute node.
#
# This script is part of the EESSI software layer, see
# https://github.com/EESSI/software-layer.git
#
# author: Thomas Roeblitz (@trz42)
#
# license: GPLv2
#

# ASSUMPTIONs:
#  - working directory has been prepared by the bot with a checkout of a
#    pull request (OR by some other means)
#  - the working directory contains a directory 'cfg' where the main config
#    file 'job.cfg' has been deposited
#  - the directory may contain any additional files referenced in job.cfg

# Debugging interactive builds
# This script respects the environment variable EESSI_INTERACTIVE_BUILD=<mode> which may be set if one wants to debug interactively
# To debug interactively, run:
# EESSI_INTERACTIVE_BUILD=<mode> <path-to-bot-build-jobscript>
# where the path-to-bot-build-jobscript is the job script referred to as build_job_script by the app.cfg of the build bot.
# That is typically eessi-bot-software-layer/scripts/bot-build.slurm, which then calls bot/build.sh (i.e. this script) with
# EESSI_INTERACTIVE_BUILD=<mode> set. This way, environment setup that is part of the bot-build.slurm script (including e.g.
# sourcing of the site_config_script as pointed to in the bot's app.cfg) is included. Then, this script sets up more of the
# build environment, before eventually running the build, dropping you in an interactive debugging shell. Supported modes:
#   shell  - start a plain shell in the build container instead of running the build
#   before - run the build, but start a shell (inside the compat layer, EasyBuild loaded) right before
#            EasyBuild is called
#   onfail - run the build, and start a shell when EasyBuild fails (or the build script exits non-zero)
# The state of the bot's build step (a tarball in previous_tmp/build_step) is resumed, and one accelerator target is
# handled. Select them with EESSI_INTERACTIVE_BUILD_RESUME_FROM (a tmp directory or tarball, or 'none' to not resume from
# anything and start from scratch) and EESSI_INTERACTIVE_BUILD_ACCELERATOR (e.g. accel/nvidia/cc80). If one of them is set, it
# is used as given. If it is not set, you are asked to choose from the available accelerator targets / tarballs (the n-th
# tarball is normally the one written after the n-th accelerator build), which needs stdin to be a terminal. Nothing is
# assumed: without a terminal the script stops with an error asking you to set the variable(s). The only choices that
# are not asked are those with just one option: a single accelerator target, or no tarballs to resume from.
# Nothing is saved, no tarball is created.

# stop as soon as something fails
set -e

# Make sure we are referring to software-layer as working directory
software_layer_dir=$(dirname $(dirname $(realpath $0)))
# source utils.sh and cfg_files.sh
source $software_layer_dir/scripts/utils.sh
source $software_layer_dir/scripts/cfg_files.sh

# defaults
export JOB_CFG_FILE="${JOB_CFG_FILE_OVERRIDE:=cfg/job.cfg}"
HOST_ARCH=$(uname -m)

# check if ${JOB_CFG_FILE} exists
if [[ ! -r "${JOB_CFG_FILE}" ]]; then
    fatal_error "job config file (JOB_CFG_FILE=${JOB_CFG_FILE}) does not exist or not readable"
fi
echo "bot/build.sh: showing ${JOB_CFG_FILE} from software-layer side"
cat ${JOB_CFG_FILE}

echo "bot/build.sh: obtaining configuration settings from '${JOB_CFG_FILE}'"
cfg_load ${JOB_CFG_FILE}

# if http_proxy is defined in ${JOB_CFG_FILE} use it, if not use env var $http_proxy
HTTP_PROXY=$(cfg_get_value "site_config" "http_proxy")
HTTP_PROXY=${HTTP_PROXY:-${http_proxy}}
echo "bot/build.sh: HTTP_PROXY='${HTTP_PROXY}'"

# if https_proxy is defined in ${JOB_CFG_FILE} use it, if not use env var $https_proxy
HTTPS_PROXY=$(cfg_get_value "site_config" "https_proxy")
HTTPS_PROXY=${HTTPS_PROXY:-${https_proxy}}
echo "bot/build.sh: HTTPS_PROXY='${HTTPS_PROXY}'"

LOCAL_TMP=$(cfg_get_value "site_config" "local_tmp")
echo "bot/build.sh: LOCAL_TMP='${LOCAL_TMP}'"
# TODO should local_tmp be mandatory? --> then we check here and exit if it is not provided

# check if path to copy build logs to is specified, so we can copy build logs for failing builds there
BUILD_LOGS_DIR=$(cfg_get_value "site_config" "build_logs_dir")
echo "bot/build.sh: BUILD_LOGS_DIR='${BUILD_LOGS_DIR}'"
# if $BUILD_LOGS_DIR is set, add it to $SINGULARITY_BIND so the path is available in the build container
if [[ ! -z ${BUILD_LOGS_DIR} ]]; then
    mkdir -p ${BUILD_LOGS_DIR}
    if [[ -z ${SINGULARITY_BIND} ]]; then
        export SINGULARITY_BIND="${BUILD_LOGS_DIR}"
    else
        export SINGULARITY_BIND="${SINGULARITY_BIND},${BUILD_LOGS_DIR}"
    fi
fi

# check if path to directory on shared filesystem is specified,
# and use it as location for source tarballs used by EasyBuild if so
SHARED_FS_PATH=$(cfg_get_value "site_config" "shared_fs_path")
echo "bot/build.sh: SHARED_FS_PATH='${SHARED_FS_PATH}'"
# if $SHARED_FS_PATH is set, add it to $SINGULARITY_BIND so the path is available in the build container
if [[ ! -z ${SHARED_FS_PATH} ]]; then
    mkdir -p ${SHARED_FS_PATH}
    if [[ -z ${SINGULARITY_BIND} ]]; then
        export SINGULARITY_BIND="${SHARED_FS_PATH}"
    else
        export SINGULARITY_BIND="${SINGULARITY_BIND},${SHARED_FS_PATH}"
    fi
fi

SINGULARITY_CACHEDIR=$(cfg_get_value "site_config" "container_cachedir")
echo "bot/build.sh: SINGULARITY_CACHEDIR='${SINGULARITY_CACHEDIR}'"
if [[ ! -z ${SINGULARITY_CACHEDIR} ]]; then
    # make sure that separate directories are used for different CPU families
    SINGULARITY_CACHEDIR=${SINGULARITY_CACHEDIR}/${HOST_ARCH}
    export SINGULARITY_CACHEDIR
fi

if [[ -z "${TMPDIR}" ]]; then
    echo -n "setting \$STORAGE by replacing any var in '${LOCAL_TMP}' -> "
    # replace any env variable in ${LOCAL_TMP} with its
    #   current value (e.g., a value that is local to the job)
    STORAGE=$(envsubst <<< ${LOCAL_TMP})
else
    STORAGE=${TMPDIR}
fi
echo "bot/build.sh: STORAGE='${STORAGE}'"

# make sure ${STORAGE} exists
mkdir -p ${STORAGE}

# make sure the base tmp storage is unique
JOB_STORAGE=$(mktemp --directory --tmpdir=${STORAGE} bot_job_tmp_XXX)
echo "bot/build.sh: created unique base tmp storage directory at ${JOB_STORAGE}"

# obtain list of modules to be loaded
LOAD_MODULES=$(cfg_get_value "site_config" "load_modules")
echo "bot/build.sh: LOAD_MODULES='${LOAD_MODULES}'"

# singularity/apptainer settings: CONTAINER, HOME, TMPDIR, BIND
CONTAINER=$(cfg_get_value "repository" "container")
export SINGULARITY_HOME="${PWD}:/eessi_bot_job"
export SINGULARITY_TMPDIR="${JOB_STORAGE}/singularity_tmpdir"
mkdir -p ${SINGULARITY_TMPDIR}

# load modules if LOAD_MODULES is not empty
if [[ ! -z ${LOAD_MODULES} ]]; then
    IFS=',' read -r -a modules <<< "$(echo "${LOAD_MODULES}")"
    for mod in "${modules[@]}";
    do
        echo "bot/build.sh: loading module '${mod}'"
        module load ${mod}
    done
else
    echo "bot/build.sh: no modules to be loaded"
fi

# determine repository to be used from entry .repository in ${JOB_CFG_FILE}
REPOSITORY_ID=$(cfg_get_value "repository" "repo_id")
REPOSITORY_NAME=$(cfg_get_value "repository" "repo_name")
REPOSITORY_VERSION=$(cfg_get_value "repository" "repo_version")
EESSI_REPOS_CFG_DIR_OVERRIDE=$(cfg_get_value "repository" "repos_cfg_dir")
export EESSI_REPOS_CFG_DIR_OVERRIDE=${EESSI_REPOS_CFG_DIR_OVERRIDE:-${PWD}/cfg}
echo "bot/build.sh: EESSI_REPOS_CFG_DIR_OVERRIDE='${EESSI_REPOS_CFG_DIR_OVERRIDE}'"

# determine EESSI version to be used from .repository.repo_version in ${JOB_CFG_FILE}
# here, just set & export EESSI_VERSION_OVERRIDE
# next script (eessi_container.sh) makes use of it via sourcing init scripts
# (e.g., init/eessi_defaults or init/minimal_eessi_env)
export EESSI_VERSION_OVERRIDE=${REPOSITORY_VERSION}
echo "bot/build.sh: EESSI_VERSION_OVERRIDE='${EESSI_VERSION_OVERRIDE}'"

# determine CVMFS repo to be used from .repository.repo_name in ${JOB_CFG_FILE}
# here, just set EESSI_CVMFS_REPO_OVERRIDE, a bit further down
# "source init/eessi_defaults" via sourcing init/minimal_eessi_env
export EESSI_CVMFS_REPO_OVERRIDE=/cvmfs/${REPOSITORY_NAME}
echo "bot/build.sh: EESSI_CVMFS_REPO_OVERRIDE='${EESSI_CVMFS_REPO_OVERRIDE}'"

# If we're not building for software.eessi.io, then consider this a site install
if [[ "${EESSI_CVMFS_REPO_OVERRIDE}" != "/cvmfs/software.eessi.io" ]]; then
    # To build on top of EESSI, we need the software.eessi.io repository to be mounted next to the target repository
    # The bot/build.sh script does this when the EESSI_SITE_INSTALL_FORCE environment variable is set
    # Other build scripts will also respect this variable where needed in order to make sure that 'building on top'
    # of EESSI is possible
    export EESSI_SITE_INSTALL_FORCE=1
    echo "EESSI_SITE_INSTALL_FORCE=$EESSI_SITE_INSTALL_FORCE"

    # We also need to set a prefix that our installations should end up in
    # The build scripts should take this prefix, and construct the final EESSI_SITE_SOFTWARE_PATH out of it
    # that the EESSI-extend module expects
    if [[ -z "${EESSI_SITE_SOFTWARE_PREFIX}" ]]; then
        export EESSI_SITE_SOFTWARE_PREFIX=$EESSI_CVMFS_REPO_OVERRIDE
    fi
    echo "EESSI_SITE_SOFTWARE_PREFIX=$EESSI_SITE_SOFTWARE_PREFIX"

    # Make sure that the compatibility layer is still used from software.eessi.io
    export EESSI_CVMFS_COMPAT_REPO=/cvmfs/software.eessi.io
    echo "EESSI_CVMFS_COMPAT_REPO=${EESSI_CVMFS_COMPAT_REPO}"

    # Determine the relative path to the versions subdir for site installations
    # by removing the /cvmfs/some.repo.tld part and appending /versions at the end
    # E.g. EESSI_SITE_SOFTWARE_PREFIX=/cvmfs/my.site.tld/eessi/builds would result in eessi/builds/versions
    # Note that this also works for dev.eessi.io builds, as these are configured as site installations
    export EESSI_VERSIONS_SUBPATH="${EESSI_SITE_SOFTWARE_PREFIX#/cvmfs/${REPOSITORY_NAME}}/versions"
else
    # For software.eessi.io the versions dir is always in the root of the repository
    export EESSI_VERSIONS_SUBPATH=versions
fi

# determine CPU architecture to be used from entry .architecture in ${JOB_CFG_FILE}
# fallbacks:
#  - ${CPU_TARGET} handed over from bot
#  - left empty to let downstream script(s) determine subdir to be used
EESSI_SOFTWARE_SUBDIR_OVERRIDE=$(cfg_get_value "architecture" "software_subdir")
EESSI_SOFTWARE_SUBDIR_OVERRIDE=${EESSI_SOFTWARE_SUBDIR_OVERRIDE:-${CPU_TARGET}}
export EESSI_SOFTWARE_SUBDIR_OVERRIDE
echo "bot/build.sh: EESSI_SOFTWARE_SUBDIR_OVERRIDE='${EESSI_SOFTWARE_SUBDIR_OVERRIDE}'"

# Log the full lscpu, ulimits, and os-release info:
lscpu > _bot_job${SLURM_JOB_ID}.lscpu
ulimit -a > _bot_job${SLURM_JOB_ID}.ulimits
cat /etc/os-release > _bot_job${SLURM_JOB_ID}.os

# Also: fetch CPU flags into an array, so that we can implement a hard check against a reference
lscpu_flags_line=$(lscpu | grep "Flags:" || echo "")
# strip leading "Flags:" and spaces, and put result in a bash array
if [[ $lscpu_flags =~ Flags:\ (.*) ]]; then lscpu_flags=(${BASH_REMATCH[1]}); fi
# for now, just print
echo "bot/build.sh: CPU flags=${lscpu_flags[@]}"
# TODO: an actual comparison with a reference bash array, e.g. through
# diff_result=$(diff <(printf "%s\n" "${lscpu_flags[@]}" | sort) <(printf "%s\n" "${lscpu_flags_ref[@]}" | sort))
# if [ ! -z "$diff_result" ]; then
#    echo "bot/build.sh: ERROR: difference between reported lscpu flags and reference for this ($EESSI_SOFTWARE_SUBDIR_OVERRIDE) CPU architecture. This could mean an incorrect build host was used to build for this target.
# fi

# get EESSI_OS_TYPE from .architecture.os_type in ${JOB_CFG_FILE} (default: linux)
EESSI_OS_TYPE=$(cfg_get_value "architecture" "os_type")
export EESSI_OS_TYPE=${EESSI_OS_TYPE:-linux}
echo "bot/build.sh: EESSI_OS_TYPE='${EESSI_OS_TYPE}'"

# prepare arguments to eessi_container.sh common to build and tarball steps
declare -a COMMON_ARGS=()
COMMON_ARGS+=("--verbose")
COMMON_ARGS+=("--access" "rw")
COMMON_ARGS+=("--mode" "exec")
[[ ! -z ${CONTAINER} ]] && COMMON_ARGS+=("--container" "${CONTAINER}")
[[ ! -z ${HTTP_PROXY} ]] && COMMON_ARGS+=("--http-proxy" "${HTTP_PROXY}")
[[ ! -z ${HTTPS_PROXY} ]] && COMMON_ARGS+=("--https-proxy" "${HTTPS_PROXY}")
[[ ! -z ${REPOSITORY_ID} ]] && COMMON_ARGS+=("--repository" "${REPOSITORY_ID}")

# Also expose software.eessi.io when building on top of EESSI (i.e. when EESSI_SITE_INSTALL_FORCE is set)
if [[ -n "${EESSI_SITE_INSTALL_FORCE}" ]]; then
    COMMON_ARGS+=("--repository" "software.eessi.io,access=ro")
fi

# Override the compat layer if EESSI_CVMFS_COMPAT_REPO is defined. This allows using a different repo for the
# compatibility layer compared to the EESSI_CVMFS_REPO (in which things will be installed)
if [[ -n "${EESSI_CVMFS_COMPAT_REPO}" && -n "${EESSI_VERSION_OVERRIDE:-$EESSI_VERSION}" ]]; then
    EESSI_COMPAT_VERSION=${EESSI_VERSION_OVERRIDE:-$EESSI_VERSION}
    # Cut off any version suffix (2025.06-001 -> 2025.06)
    EESSI_COMPAT_VERSION=${EESSI_COMPAT_VERSION%-*}
    export EESSI_COMPAT_LAYER_DIR_OVERRIDE="${EESSI_CVMFS_COMPAT_REPO}/versions/${EESSI_COMPAT_VERSION}/compat/linux/$(uname -m)"
    msg="bot:build.sh: Set EESSI_COMPAT_LAYER_DIR_OVERRIDE to $EESSI_COMPAT_LAYER_DIR_OVERRIDE since both EESSI_CVMFS_COMPAT_REPO"
    msg="$msg (${EESSI_CVMFS_COMPAT_REPO}) and EESSI_VERSION_OVERRIDE (${EESSI_VERSION_OVERRIDE}) are defined"
    echo "$msg"
else
    echo "bot/build.sh: EESSI_CVMFS_COMPAT_REPO: ${EESSI_CVMFS_COMPAT_REPO}"
    echo "bot/build.sh: EESSI_VERSION_OVERRIDE: ${EESSI_VERSION_OVERRIDE}"
fi

# add $software_layer_dir and /dev as extra bind paths
#  - $software_layer_dir is needed because it is used as prefix for running scripts
#  - /dev is needed to access /dev/fuse
COMMON_ARGS+=("--extra-bind-paths" "${software_layer_dir},/dev")

# pass through '--contain' to avoid leaking in scripts into the container session
# note, --pass-through can be used multiple times if needed
COMMON_ARGS+=("--pass-through" "--contain")

# make sure to use the same parent dir for storing tarballs of tmp
PREVIOUS_TMP_DIR=${PWD}/previous_tmp

# prepare directory to store tarball of tmp for build step
TARBALL_TMP_BUILD_STEP_DIR=${PREVIOUS_TMP_DIR}/build_step
mkdir -p ${TARBALL_TMP_BUILD_STEP_DIR}

# prepare arguments to eessi_container.sh specific to build step
declare -a BUILD_STEP_ARGS=()
# We don't want interactive debugging sessions to save tarballs, so only save if EESSI_INTERACTIVE_BUILD isn't set
if [[ -z "${EESSI_INTERACTIVE_BUILD}" ]]; then
    BUILD_STEP_ARGS+=("--save" "${TARBALL_TMP_BUILD_STEP_DIR}")
fi
BUILD_STEP_ARGS+=("--storage" "${STORAGE}")

# interactive debugging: check the requested mode. The state of the bot's build step to resume from (tarball) and the
# accelerator target are selected further below, once the list of accelerator targets is known
if [[ -n "${EESSI_INTERACTIVE_BUILD}" ]]; then
    if [[ ! "${EESSI_INTERACTIVE_BUILD}" =~ ^(shell|before|onfail)$ ]]; then
        fatal_error "EESSI_INTERACTIVE_BUILD must be one of: shell, before, onfail (got '${EESSI_INTERACTIVE_BUILD}')"
    fi
fi

# Retain location for host injections so we don't reinstall CUDA
# (Always need to run the driver installation as available driver may change)
if [[ ! -z ${SHARED_FS_PATH} ]]; then
    BUILD_STEP_ARGS+=("--host-injections" "${SHARED_FS_PATH}/host-injections")
fi

# prepare arguments to install_software_layer.sh (specific to build step)
declare -a INSTALL_SCRIPT_ARGS=()
if [[ ${EESSI_SOFTWARE_SUBDIR_OVERRIDE} =~ .*/generic$ ]]; then
    INSTALL_SCRIPT_ARGS+=("--generic")
fi
[[ ! -z ${BUILD_LOGS_DIR} ]] && INSTALL_SCRIPT_ARGS+=("--build-logs-dir" "${BUILD_LOGS_DIR}")
[[ ! -z ${SHARED_FS_PATH} ]] && INSTALL_SCRIPT_ARGS+=("--shared-fs-path" "${SHARED_FS_PATH}")
# Skip CUDA installation for RISC-V builds
if [[ "${REPOSITORY_NAME}" == "riscv.eessi.io" || "${EESSI_SOFTWARE_SUBDIR_OVERRIDE}" =~ ^riscv64/.* ]]; then
    echo "bot/build.sh: disabling CUDA installation for RISC-V builds"
    INSTALL_SCRIPT_ARGS+=("--skip-cuda-install")
fi

# create tmp file for output of build step
build_outerr=$(mktemp build.outerr.XXXX)

# determine accelerator target (if any) from .architecture in ${JOB_CFG_FILE}
ACCEL_OVERRIDES=$(cfg_get_value "architecture" "accelerator")
if [[ -z ${ACCEL_OVERRIDES} ]]; then
    EESSI_ACCELERATOR_TARGET_OVERRIDES=("")
else
    IFS='+' read -ra ACCEL_OVERRIDES_ARRAY <<< "$ACCEL_OVERRIDES"
    # prepend accel/ to all array elements
    EESSI_ACCELERATOR_TARGET_OVERRIDES=("${ACCEL_OVERRIDES_ARRAY[@]/#/accel/}")
fi
RESUME_DIR=""

# interactive debugging only handles a single accelerator target, and resumes from a single tarball of the bot's build step
# (or from none). Select them as described at the top of this script, see scripts/interactive_build.sh
if [[ -n "${EESSI_INTERACTIVE_BUILD}" ]]; then
    source $software_layer_dir/scripts/interactive_build.sh
    select_interactive_state
    if [[ "${selected_tarball}" == "none" ]]; then
        echo "bot/build.sh: EESSI_INTERACTIVE_BUILD='${EESSI_INTERACTIVE_BUILD}', accelerator '${selected_accel_target}', not resuming from a tarball"
    else
        echo "bot/build.sh: EESSI_INTERACTIVE_BUILD='${EESSI_INTERACTIVE_BUILD}', accelerator '${selected_accel_target}', resuming from '${selected_tarball}'"
        BUILD_STEP_ARGS+=("--resume" "${selected_tarball}")
    fi
    EESSI_ACCELERATOR_TARGET_OVERRIDES=("${selected_accel_target}")
fi

for ACCEL_OVERRIDE in "${EESSI_ACCELERATOR_TARGET_OVERRIDES[@]}"; do
    # copy the common build step arguments to a a
    BUILD_STEP_ARGS_ACCEL=("${BUILD_STEP_ARGS[@]}")
    if [[ "${ACCEL_OVERRIDE}" == "accel/nvidia/"* ]]; then
        nvidia_cc=${ACCEL_OVERRIDE##*/cc}
        # add options required to handle NVIDIA support
        # only make the GPU available in the container if the host has a GPU and it has the correct compute capability
        if nvidia_gpu_available && nvidia_gpu_has_compute_capability "${nvidia_cc}" ; then
            echo "bot/build.sh: GPU with the requested compute capability is available, using '--nvidia all'"
            BUILD_STEP_ARGS_ACCEL+=("--nvidia" "all")
        else
            echo "bot/build.sh: no GPU with the requested compute capability is available, using '--nvidia install'"
            BUILD_STEP_ARGS_ACCEL+=("--nvidia" "install")
        fi
    fi
    # resume from the previous accelerator's build directory
    # as we want to combine all accelerator builds into a single tarball in the end
    if [[ ! -z "${RESUME_DIR}" ]]; then
        BUILD_STEP_ARGS_ACCEL+=("--resume" "${RESUME_DIR}")
    fi

    export EESSI_ACCELERATOR_TARGET_OVERRIDE="${ACCEL_OVERRIDE}"
    echo "bot/build.sh: EESSI_ACCELERATOR_TARGET_OVERRIDE='${ACCEL_OVERRIDE}'"
    echo "Executing command to build software:"
    echo "$software_layer_dir/eessi_container.sh ${COMMON_ARGS[@]} ${BUILD_STEP_ARGS_ACCEL[@]}"
    echo "                     -- $software_layer_dir/install_software_layer.sh \"${INSTALL_SCRIPT_ARGS[@]}\" \"$@\" 2>&1 | tee -a ${build_outerr}"
    if [[ -n "${EESSI_INTERACTIVE_BUILD}" ]]; then
        # Interactive debugging: same setup as above. No 'tee' as it would break the interactive terminal.
        if [[ "${EESSI_INTERACTIVE_BUILD}" == "shell" ]]; then
            echo "bot/build.sh: starting a shell in the build container instead of the build; to run the build step manually use:"
            echo "                     $software_layer_dir/install_software_layer.sh ${INSTALL_SCRIPT_ARGS[@]} $@"
            # a later --mode overrides the --mode exec in COMMON_ARGS
            $software_layer_dir/eessi_container.sh "${COMMON_ARGS[@]}" "${BUILD_STEP_ARGS_ACCEL[@]}" --mode shell
        else
            $software_layer_dir/eessi_container.sh "${COMMON_ARGS[@]}" "${BUILD_STEP_ARGS_ACCEL[@]}" \
                                 -- $software_layer_dir/install_software_layer.sh "${INSTALL_SCRIPT_ARGS[@]}" "$@"
        fi
        exit 0
    fi
    $software_layer_dir/eessi_container.sh "${COMMON_ARGS[@]}" "${BUILD_STEP_ARGS_ACCEL[@]}" \
                         -- $software_layer_dir/install_software_layer.sh "${INSTALL_SCRIPT_ARGS[@]}" "$@" 2>&1 | tee -a ${build_outerr}

    # determine temporary directory to resume from for the next accelerator,
    RESUME_DIR=$(grep ' as tmp directory ' ${build_outerr} | cut -d ' ' -f 2)
done

# prepare directory to store tarball of tmp for tarball step
TARBALL_TMP_TARBALL_STEP_DIR=${PREVIOUS_TMP_DIR}/tarball_step
mkdir -p ${TARBALL_TMP_TARBALL_STEP_DIR}

# create tmp file for output of tarball step
tar_outerr=$(mktemp tar.outerr.XXXX)

# prepare arguments to eessi_container.sh specific to tarball step
declare -a TARBALL_STEP_ARGS=()
TARBALL_STEP_ARGS+=("--save" "${TARBALL_TMP_TARBALL_STEP_DIR}")

# determine temporary directory to resume from
BUILD_TMPDIR=$(grep ' as tmp directory ' ${build_outerr} | cut -d ' ' -f 2)
TARBALL_STEP_ARGS+=("--resume" "${BUILD_TMPDIR}")

timestamp=$(date +%s)
# determine compression/extension for tarball, check in order of preference
if [[ -x "$(command -v zstd)" ]]; then
    tarball_extension="tar.zst"
elif [[ -x "$(command -v gzip)" ]]; then
    tarball_extension="tar.gz"
else
    tarball_extension="tar"
fi
# to set EESSI_VERSION we need to source init/eessi_defaults now
source $software_layer_dir/init/eessi_defaults
# Note: if ${EESSI_DEV_PROJECT} is defined (building for dev.eessi.io), then we
# append the project (subdirectory) name to the end tarball name. This is information
# then used at the ingestion stage. If ${EESSI_DEV_PROJECT} is not defined, nothing is
# appended
if [[ -z ${ACCEL_OVERRIDES} ]]; then
    export TARBALL=$(printf "eessi-%s-software-%s-%s-%b%d.${tarball_extension}" ${EESSI_VERSION} ${EESSI_OS_TYPE} ${EESSI_SOFTWARE_SUBDIR_OVERRIDE//\//-} ${EESSI_DEV_PROJECT:+$EESSI_DEV_PROJECT-} ${timestamp})
else
    # replace slashes in accelerator names by a hyphen, and concatenate them into a hypen-separated string
    filename_accelerators=$(IFS=-; echo "${EESSI_ACCELERATOR_TARGET_OVERRIDES[*]//\//-}")
    export TARBALL=$(printf "eessi-%s-software-%s-%s-%s-%b%d.${tarball_extension}" ${EESSI_VERSION} ${EESSI_OS_TYPE} ${EESSI_SOFTWARE_SUBDIR_OVERRIDE//\//-} ${filename_accelerators} ${EESSI_DEV_PROJECT:+$EESSI_DEV_PROJECT-} ${timestamp})
fi

# value of first parameter to create_tarball.sh - TMP_IN_CONTAINER - needs to be
# synchronised with setting of TMP_IN_CONTAINER in eessi_container.sh
# TODO should we make this a configurable parameter of eessi_container.sh using
# /tmp as default?
TMP_IN_CONTAINER=/tmp
tarball_accelerators=$(IFS=+; echo "${EESSI_ACCELERATOR_TARGET_OVERRIDES[*]}")
echo "Executing command to create tarball:"
echo "$software_layer_dir/eessi_container.sh ${COMMON_ARGS[@]} ${TARBALL_STEP_ARGS[@]}"
echo "                     -- $software_layer_dir/create_tarball.sh ${TMP_IN_CONTAINER} ${EESSI_VERSIONS_SUBPATH} ${EESSI_VERSION}${EESSI_SOFTWARE_LAYER_VERSION_SUFFIX} ${EESSI_SOFTWARE_SUBDIR_OVERRIDE} \"$tarball_accelerators\" /eessi_bot_job/${TARBALL} 2>&1 | tee -a ${tar_outerr}"
$software_layer_dir/eessi_container.sh "${COMMON_ARGS[@]}" "${TARBALL_STEP_ARGS[@]}" \
                     -- $software_layer_dir/create_tarball.sh ${TMP_IN_CONTAINER} ${EESSI_VERSIONS_SUBPATH} ${EESSI_VERSION}${EESSI_SOFTWARE_LAYER_VERSION_SUFFIX} ${EESSI_SOFTWARE_SUBDIR_OVERRIDE} "$tarball_accelerators" /eessi_bot_job/${TARBALL} 2>&1 | tee -a ${tar_outerr}

exit 0
