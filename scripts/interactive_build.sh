# Functions for debugging a bot build interactively, sourced by bot/build.sh if EESSI_INTERACTIVE_BUILD is set.
# See the comments at the top of bot/build.sh for the user-facing description.
#
# Requires fatal_error (scripts/utils.sh).

# Ask the user to choose a number between $2 (lowest number) and $3 (highest number) for $1 (description of what is
# chosen). The result is returned in CHOSEN_NUMBER. There is no default: an empty answer is invalid. Returns 1 if no
# answer can be read (e.g. at EOF because user pressed Ctrl+D / terminal disappears, which also avoids an infinite loop).
# Note that all output of the dialog goes to stderr, like the prompt of 'read -p' does (bash's own convention for prompts)
interactive_choice() {
    local description="$1" lowest_number="$2" highest_number="$3" answer
    # loop until we get a valid answer
    while true; do
        if ! read -r -p "bot/build.sh: select ${description} [${lowest_number}-${highest_number}]: " answer; then
            echo >&2
            return 1
        fi
        # Check answer is valid and return
        if [[ "${answer}" =~ ^[0-9]+$ ]] && (( answer >= lowest_number && answer <= highest_number )); then
            CHOSEN_NUMBER=${answer}
            return
        fi
        echo "bot/build.sh: invalid choice '${answer}'" >&2
    done
}

# Choose one entry of the array named $4, and return its number in CHOSEN_NUMBER: N for entry N of the array (counting from
# 1), and 0 for 'none' (only allowed if $3, the lowest number, is 0).
#   $1 description of what is chosen, $2 name of the environment variable with which the user can set it
# If there is only one option, we don't need to choose. Otherwise we ask the user, if stdin is a terminal, or else stop
# with an error: we don't assume anything.
choose_option() {
    local description="$1" env_var_name="$2" lowest_number="$3"
    local -n option_array="$4"
    local highest_number=${#option_array[@]}
    if [[ ${highest_number} -eq ${lowest_number} ]]; then
        CHOSEN_NUMBER=${highest_number}
    elif [[ -t 0 ]]; then
        interactive_choice "${description}" ${lowest_number} ${highest_number} ||
            fatal_error "no answer given for ${description}, set ${env_var_name} to choose it without being asked"
    else
        fatal_error "stdin is not a terminal, so cannot ask which ${description}, set ${env_var_name} to choose it"
    fi
}

# Select the accelerator target and the tarball of the bot's build step to resume from, based on
# EESSI_INTERACTIVE_BUILD_ACCELERATOR and EESSI_INTERACTIVE_BUILD_RESUME_FROM, asking for what isn't set (see bot/build.sh).
# Needs EESSI_ACCELERATOR_TARGET_OVERRIDES (the accelerator targets of the build) and TARBALL_TMP_BUILD_STEP_DIR.
# Returns the accelerator target in selected_accel_target, and the tarball (or directory) in selected_tarball, which is
# the word 'none' if we shouldn't resume from anything.
select_interactive_state() {
    local -a accel_targets=("${EESSI_ACCELERATOR_TARGET_OVERRIDES[@]}")
    local -a tarball_paths
    mapfile -t tarball_paths < <(ls -tr "${TARBALL_TMP_BUILD_STEP_DIR}"/tmp_storage-* 2>/dev/null)
    local i

    # select the accelerator target
    if [[ -n "${EESSI_INTERACTIVE_BUILD_ACCELERATOR}" ]]; then
        # Format for EESSI_INTERACTIVE_BUILD_ACCELERATOR is similar to EESSI_ACCELERATOR_TARGET_OVERRIDE,
        # e.g. accel/nvidia/cc80
        selected_accel_target="${EESSI_INTERACTIVE_BUILD_ACCELERATOR}"
        local found=0 accel_target
        for accel_target in "${accel_targets[@]}"; do
            [[ "${accel_target}" == "${selected_accel_target}" ]] && found=1
        done
        if [[ ${found} -eq 0 ]]; then
            fatal_error "EESSI_INTERACTIVE_BUILD_ACCELERATOR='${EESSI_INTERACTIVE_BUILD_ACCELERATOR}' not found in accelerator targets of ${JOB_CFG_FILE}: ${accel_targets[*]}"
        fi
    else
        # List accelerators from the job.cfg file
        # (skip listing if there is only one - choose_option will select it by default)
        if [[ ${#accel_targets[@]} -gt 1 ]]; then
            echo "bot/build.sh: accelerator targets of this build (in the order the bot built them):"
            for i in "${!accel_targets[@]}"; do
                echo "  [$((i+1))] '${accel_targets[$i]}'"
            done
        fi
        choose_option "accelerator target" EESSI_INTERACTIVE_BUILD_ACCELERATOR 1 accel_targets
        selected_accel_target="${accel_targets[$((CHOSEN_NUMBER-1))]}"
    fi

    # select the tarball to resume from, or none
    if [[ -n "${EESSI_INTERACTIVE_BUILD_RESUME_FROM}" ]]; then
        selected_tarball="${EESSI_INTERACTIVE_BUILD_RESUME_FROM}"
    else
        echo "bot/build.sh: tarballs of the bot's build step in ${TARBALL_TMP_BUILD_STEP_DIR} (oldest first):"
        echo "  [0] none (do not resume from a tarball, start from scratch)"
        for i in "${!tarball_paths[@]}"; do
            echo "  [$((i+1))] $(basename "${tarball_paths[$i]}")   ($(date -r "${tarball_paths[$i]}" '+%Y-%m-%d %H:%M'), $(du -h "${tarball_paths[$i]}" | cut -f1))"
        done
        echo "  note: normally, tarball [n] is written after the n-th accelerator build, since each build should produce one tarball"
        [[ ${#tarball_paths[@]} -gt 0 ]] || echo "  no tarballs found, so not resuming from a tarball"
        choose_option "tarball to resume from (0 = none)" EESSI_INTERACTIVE_BUILD_RESUME_FROM 0 tarball_paths
        selected_tarball="none"
        [[ ${CHOSEN_NUMBER} -gt 0 ]] && selected_tarball="${tarball_paths[$((CHOSEN_NUMBER-1))]}"
    fi
    # accept 'none' in any capitalisation (and normalize to lower case for easier subsequent checks)
    [[ "${selected_tarball,,}" == "none" ]] && selected_tarball="none"
    if [[ "${selected_tarball}" != "none" && ! -e "${selected_tarball}" ]]; then
        fatal_error "state to resume from does not exist: ${selected_tarball}"
    fi
}
