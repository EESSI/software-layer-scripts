#!/bin/bash
check_env_var() {
  # Expected usage: check_env_var "MY_ENV_VAR" "foo"
  var_name="$1"
  expected="$2"
  actual="${!var_name}"
  if [ "$actual" != "$expected" ]; then
    echo "ERROR: $var_name is '$actual', expected '$expected'" >&2
    exit 1
  else
    echo "$var_name is correctly set to '$expected'"
  fi
}

check_disallowed_env_prefix() {
  prefix="$1"
  shift
  whitelist=("$@")

  disallowed=()

  while IFS='=' read -r var _; do
    if [[ "$var" == "$prefix"* ]]; then
      allowed=false
      for allowed_var in "${whitelist[@]}"; do
        if [[ "$var" == "$allowed_var" ]]; then
          allowed=true
          break
        fi
      done

      if ! $allowed; then
        disallowed+=("$var")
      fi
    fi
  done < <(env)

  if [ "${#disallowed[@]}" -ne 0 ]; then
    echo "ERROR: Found disallowed environment variables with prefix '$prefix':" >&2
    for var in "${disallowed[@]}"; do
      echo "  - $var" >&2
    done
    exit 1
  else
    echo "✅ No disallowed environment variables with prefix '$prefix' found."
  fi
}

init_eessi_environment() {
  # Helper function to initialize the EESSI environment, and optionally add
  # a path to the modulepath in which EESSI-extend has made local installs
  # Expected usage:
  #  init_eessi_environment --eessi-version <version> [--installation-path <path>] \
  #                         [--accelerator-target <target>] [--cuda-cc <n>]
  local eessi_version=""
  local eessi_extend_installation_path=""
  local accelerator_target_override=""
  local cuda_cc=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --eessi-version)
        eessi_version="$2"
        shift 2
        ;;
      --installation-path)
        eessi_extend_installation_path="$2"
        shift 2
        ;;
      --accelerator-target)
        accelerator_target_override="$2"
        shift 2
        ;;
      --cuda-cc)
        cuda_cc="$2"
        shift 2
        ;;
      *)
        echo "ERROR: Unknown option '$1' passed to init_eessi_environment" >&2
        return 1
        ;;
    esac
  done

  if [ -z "$eessi_version" ]; then
    echo "ERROR: --eessi-version is required" >&2
    return 1
  fi

  # Let's start from a clean slate
  module --force purge
  if [ -n "$accelerator_target_override" ]; then
    export EESSI_ACCELERATOR_TARGET_OVERRIDE="$accelerator_target_override"
  fi
  # Load the EESSI module
  module load EESSI/$eessi_version
  # Access the installed EESSI-extend (if an installation path was provided)
  if [ -n "$eessi_extend_installation_path" ]; then
    module use "$eessi_extend_installation_path"/modules/all
  fi
  check_disallowed_env_prefix EASYBUILD_
}
