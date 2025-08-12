#!/bin/bash
#
# Script to install scripts from the software-layer repo into the EESSI software stack

display_help() {
  echo "usage: $0 [OPTIONS]"
  echo "  -p | --prefix          -  prefix to copy the scripts to"
  echo "  -h | --help            -  display this usage information"
}

file_changed_in_pr() {
  local full_path="$1"

  # Make sure file exists
  [[ -f "$full_path" ]] || return 1

  # Check if the file is in a Git repo (it should be) 
  local repo_root
  repo_root=$(git -C "$(dirname "$full_path")" rev-parse --show-toplevel 2>/dev/null)
  if [[ -z "$repo_root" ]]; then
    return 2  # Not in a git repository
  fi

  # Compute relative path to the repo root
  local rel_path
  rel_path=$(realpath --relative-to="$repo_root" "$full_path")

  # Check if the file changed in the PR diff file that we have
  (
    cd "$repo_root" || return 2
    # $PR_DIFF should be set by the calling script
    if [[ ! -z ${PR_DIFF} ]] && [[ -f "$PR_DIFF" ]]; then
      grep -q "b/$rel_path" "$PR_DIFF" # Add b/ to match diff patterns
    else
      return 3
    fi
  ) && return 0 || return 1
}

sed_update_if_changed() {
    # Usage: sed_update_if_changed 's/foo/bar/' file.txt
    if [ "$#" -ne 2 ]; then
        echo "Usage: sed_update_if_changed 'sed_command' file" >&2
        return 1
    fi

    local sed_command="$1"
    local file="$2"
    local tmp_file="$(mktemp "${file}.XXXXXX")"

    sed "$sed_command" "$file" > "$tmp_file" || {
        rm -f "$tmp_file"
        echo "sed command failed" >&2
        return 1
    }

    if ! diff -q "$file" "$tmp_file" > /dev/null; then
        # Use cat to retain existing permissions, set umask to world readable in case the target file does not yet exist. 
        (umask 022 && cat "$tmp_file" > "$file")
    fi
    # Remove the temporary file
    rm -f "$tmp_file"
}

compare_and_copy() {
    if [ "$#" -ne 2 ]; then
        echo "Usage of function: compare_and_copy <source_file> <destination_file>"
        return 1
    fi

    source_file="$1"
    destination_file="$2"

    if [ ! -f "$destination_file" ] || ! diff -q "$source_file" "$destination_file" ; then
        echo "Files $source_file and $destination_file differ, checking if we should copy or not"
        # We only copy if the file is part of the PR
        if [ ! -f "${destination_file}" ] || file_changed_in_pr "$source_file"; then
          if [ ! -f "${destination_file}" ]; then
            echo "File has not been copied yet ($destination_file does not exist}"
          else
            echo "File has changed in the PR"
          fi
          # Use cat to retain existing permissions, set umask to world readable in case the target file does not yet exist.
          (umask 022 && cat "$source_file" > "$destination_file")
          echo "File $source_file copied to $destination_file"
        else
          case $? in
            1) echo "❌ File has NOT changed in PR" ;;
            2) echo "🚫 Not in Git repository" ;;
            3) echo "🚫 No PR diff file found" ;;
            *) echo "⚠️ Unknown error" ;;
          esac
        fi
    else
        echo "Files $1 and $2 are identical. No copy needed."
    fi
}

copy_files_by_list() {
# Compares and copies listed files from a source to a target directory
    if [ ! "$#" -ge 3 ]; then
        echo "Usage of function: copy_files_by_list <source_dir> <destination_dir> <file_list>"
        echo "Here, file_list is an (expanded) bash array"
        echo "Example:"
        echo "my_files=(file1 file2)"
        echo 'copy_files_by_list /my/source /my/target "${my_files[@]}"'
        return 1
    fi
    source_dir="$1"
    target_dir="$2"
    # Need to shift all arguments to the left twice. Then, rebuild the array with the rest of the arguments
    shift
    shift
    file_list=("$@")

    # Create target dir
    mkdir -p ${target_dir}

    # Copy from source to target
    echo "Copying files: ${file_list[@]}"
    echo "From directory: ${source_dir}"
    echo "To directory: ${target_dir}"

    for file in ${file_list[@]}; do
        compare_and_copy ${source_dir}/${file} ${target_dir}/${file}
    done
}

POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --eessi-version)
      EESSI_VERSION="$2"
      shift 2
      ;;
    -p|--prefix)
      INSTALL_PREFIX="$2"
      shift 2
      ;;
    -h|--help)
      display_help  # Call your function
      # no shifting needed here, we're done.
      exit 0
      ;;
    -*|--*)
      echo "Error: Unknown option: $1" >&2
      exit 1
      ;;
    *)  # No more options
      POSITIONAL_ARGS+=("$1") # save positional arg
      shift
      ;;
  esac
done

if [ -z "${INSTALL_PREFIX}" ]; then
    echo "EESSI prefix not specified, you must use --prefix" >&2
    exit 2
fi

if [ -z "${EESSI_VERSION}" ]; then
    echo "EESSI version not specified, you must use --eessi-version" >&2
    exit 3
fi

set -- "${POSITIONAL_ARGS[@]}"

TOPDIR=$(dirname $(realpath $0))

# Copy for init directory
init_files=(
    bash eessi_archdetect.sh eessi_defaults eessi_environment_variables eessi_software_subdir_for_host.py
    minimal_eessi_env README.md test.py lmod_eessi_archdetect_wrapper.sh lmod_eessi_archdetect_wrapper_accel.sh

)
copy_files_by_list ${TOPDIR}/init ${INSTALL_PREFIX}/init "${init_files[@]}"

# Copy for the init/arch_specs directory
arch_specs_files=(
   eessi_arch_arm.spec eessi_arch_ppc.spec eessi_arch_riscv.spec eessi_arch_x86.spec
)
copy_files_by_list ${TOPDIR}/init/arch_specs ${INSTALL_PREFIX}/init/arch_specs "${arch_specs_files[@]}"

# Copy for init/Magic_castle directory
mc_files=(
   bash eessi_python3
)
copy_files_by_list ${TOPDIR}/init/Magic_Castle ${INSTALL_PREFIX}/init/Magic_Castle "${mc_files[@]}"

# Copy for init/modules/EESSI directory
mc_files=(
   ${EESSI_VERSION}.lua
)
copy_files_by_list ${TOPDIR}/init/modules/EESSI ${INSTALL_PREFIX}/init/modules/EESSI "${mc_files[@]}"

# Copy for init/lmod directory
init_script_files=(
    bash zsh ksh fish csh    
)
copy_files_by_list ${TOPDIR}/init/lmod ${INSTALL_PREFIX}/init/lmod "${init_script_files[@]}"

# Copy for the scripts directory
script_files=(
    utils.sh
)
copy_files_by_list ${TOPDIR}/scripts ${INSTALL_PREFIX}/scripts "${script_files[@]}"

# Copy files for the scripts/gpu_support/nvidia directory
nvidia_files=(
    install_cuda_and_libraries.sh
    install_cuda_host_injections.sh
    link_nvidia_host_libraries.sh
)
copy_files_by_list ${TOPDIR}/scripts/gpu_support/nvidia ${INSTALL_PREFIX}/scripts/gpu_support/nvidia "${nvidia_files[@]}"

# Easystacks to be used to install software in host injections for this EESSI version
host_injections_easystacks_dir=${TOPDIR}/scripts/gpu_support/nvidia/easystacks/${EESSI_VERSION}
if [[ -d ${host_injections_easystacks_dir} ]]; then
    host_injections_easystacks=$(find ${host_injections_easystacks_dir} -name eessi-${EESSI_VERSION}-*-CUDA-host-injections.yml -exec basename {} \;)
    copy_files_by_list ${host_injections_easystacks_dir} ${INSTALL_PREFIX}/scripts/gpu_support/nvidia/easystacks "${host_injections_easystacks[@]}"
fi

# Copy over EasyBuild hooks file used for installations
hook_files=(
    eb_hooks.py
)
copy_files_by_list ${TOPDIR} ${INSTALL_PREFIX}/init/easybuild "${hook_files[@]}"

# replace version placeholders in scripts;
# note: the commands below are always run, regardless of whether the scripts were changed,
# but that should be fine (no changes are made if version placeholder is not present anymore)

# make sure that scripts in init/ and scripts/ use correct EESSI version
sed_update_if_changed "s/__EESSI_VERSION_DEFAULT__/${EESSI_VERSION}/g" ${INSTALL_PREFIX}/init/eessi_defaults

# replace placeholder for default EESSI version in Lmod init scripts
for shell in $(ls ${INSTALL_PREFIX}/init/lmod); do
    sed_update_if_changed "s/__EESSI_VERSION_DEFAULT__/${EESSI_VERSION}/g" ${INSTALL_PREFIX}/init/lmod/${shell}
done

# replace EESSI version used in comments in EESSI module
sed_update_if_changed "s@/<EESSI_VERSION>/@/${EESSI_VERSION}/@g" ${INSTALL_PREFIX}/init/modules/EESSI/${EESSI_VERSION}.lua

# replace EESSI version used in EasyBuild hooks
sed_update_if_changed "s@/eessi-<EESSI_VERSION>/@/eessi-${EESSI_VERSION}/@g" ${INSTALL_PREFIX}/init/easybuild/eb_hooks.py
