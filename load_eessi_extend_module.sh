# Script to load the environment module for EESSI-extend.
# If that module is not available yet, a specific version will be installed using the latest EasyBuild.
#
# This script must be sourced, since it makes changes in the current environment, like loading an EESSI-extend module.
#
# Assumptions (if one is not satisfied the script prints a message and exits)
# - EESSI version is given as first argument
# - TMPDIR is set
# - EB is set
# - EASYBUILD_INSTALLPATH needs to be set
# - Function check_exit_code is defined;
#   scripts/utils.sh in EESSI/software-layer repository defines this function, hence
#   scripts/utils.sh shall be sourced before this script is run
#
# This script is part of the EESSI software layer, see
# https://github.com/EESSI/software-layer.git
#
# author: Kenneth Hoste (@boegel, HPC-UGent)
# author: Alan O'Cais (@ocaisa, CECAM)
# author: Thomas Roeblitz (@trz42, University of Bergen)
#
# license: GPLv2
#
#
set -o pipefail

EESSI_EXTEND_EASYCONFIG="EESSI-extend-easybuild.eb"

# this script is *sourced*, not executed, so can't rely on $0 to determine path to self or script name
# $BASH_SOURCE points to correct path or script name, see also http://mywiki.wooledge.org/BashFAQ/028
if [ $# -ne 1 ]; then
    echo "Usage: source ${BASH_SOURCE} <EESSI-extend version>" >&2
    exit 1
fi

EESSI_EXTEND_VERSION="${1}-easybuild"

# make sure that environment variables that we expect to be set are indeed set
if [ -z "${TMPDIR}" ]; then
    echo "\$TMPDIR is not set; exiting" >&2
    exit 2
fi

# ${EB} is used to specify which 'eb' command should be used;
# can potentially be more than just 'eb', for example when using 'eb --optarch=GENERIC'
if [ -z "${EB}" ]; then
    echo "\$EB is not set; exiting" >&2
    exit 2
fi

# ${EASYBUILD_INSTALLPATH} points to the installation path and needs to be set
if [ -z "${EASYBUILD_INSTALLPATH}" ]; then
    echo "\$EASYBUILD_INSTALLPATH is not set; exiting" >&2
    exit 2
fi

# make sure that utility functions are defined (cfr. scripts/utils.sh script in EESSI/software-layer repo)
type check_exit_code
if [ $? -ne 0 ]; then
    echo "check_exit_code function is not defined; exiting" >&2
    exit 3
fi

echo ">> Checking for EESSI-extend module..."

ml_av_eessi_extend_out=${TMPDIR}/ml_av_eessi_extend.out
# need to use --ignore_cache to avoid the case that the module was removed (to be
# rebuilt) but it is still in the cache
module --ignore_cache avail 2>&1 | grep -i EESSI-extend/${EESSI_EXTEND_VERSION} &> ${ml_av_eessi_extend_out}
if [[ $? -eq 0 ]]; then
    echo_green ">> Module for EESSI-extend/${EESSI_EXTEND_VERSION} found!"
    install_eessi_extend=false
    rebuild_eessi_extend=false
    # $PR_DIFF should be set by the calling script (EESSI-install-software.sh)
    if [[ ! -z ${PR_DIFF} ]] && [[ -f "$PR_DIFF" ]]; then
        # check if EESSI-extend easyconfig was modified; if so, we need to rebuild it
        grep -q "^+++ b/${EESSI_EXTEND_EASYCONFIG}" "${PR_DIFF}"
        if [[ $? -eq 0 ]]; then
            rebuild_eessi_extend=true
        fi
    fi
else
    echo_yellow ">> No module yet for EESSI-extend/${EESSI_EXTEND_VERSION}, installing it..."
    install_eessi_extend=true
    rebuild_eessi_extend=false
fi

if [ "${install_eessi_extend}" = true ] || [ "${rebuild_eessi_extend}" = true ]; then

    EB_TMPDIR=${TMPDIR}/ebtmp
    echo ">> Using temporary installation of EasyBuild (in ${EB_TMPDIR})..."
    pip_install_out=${TMPDIR}/pip_install.out
    pip3 install --prefix ${EB_TMPDIR} easybuild &> ${pip_install_out}

    # keep track of original $PATH and $PYTHONPATH values, so we can restore them
    ORIG_PATH=${PATH}
    ORIG_PYTHONPATH=${PYTHONPATH}

    # minimally configure easybuild to get EESSI-extend in the right place
    (
        export EASYBUILD_PREFIX=${TMPDIR}/easybuild
        export EASYBUILD_READ_ONLY_INSTALLDIR=1

        echo ">> Final installation in ${EASYBUILD_INSTALLPATH}..."
        export PATH=${EB_TMPDIR}/bin:${PATH}
        export PYTHONPATH=$(ls -d ${EB_TMPDIR}/lib/python*/site-packages):${PYTHONPATH}
        # EESSI-extend also needs EasyBuild to be installed as a module, so install the latest release
        eb_install_out=${TMPDIR}/eb_install.out
        ok_msg="Latest EasyBuild installed, let's go!"
        fail_msg="Installing latest EasyBuild failed, that's not good... (output: ${eb_install_out})"
        ${EB} --install-latest-eb-release 2>&1 | tee ${eb_install_out}
        check_exit_code $? "${ok_msg}" "${fail_msg}"
        # Now install EESSI-extend
        eessi_install_out=${TMPDIR}/eessi_install.out
        ok_msg="EESSI-extend/${EESSI_EXTEND_VERSION} installed, let's go!"
        fail_msg="Installing EESSI-extend/${EESSI_EXTEND_VERSION} failed, that's not good... (output: ${eessi_install_out})"

        eb_args=""
        if [ "${rebuild_eessi_extend}" = true ]; then
            eb_args+="--rebuild"
        fi
        echo ">> Installing EESSI-extend with '${EB} ${eb_args} ${EESSI_EXTEND_EASYCONFIG}'..."
        ${EB} ${eb_args} "${EESSI_EXTEND_EASYCONFIG}" 2>&1 | tee ${eessi_install_out}
        check_exit_code $? "${ok_msg}" "${fail_msg}"
    )

    # restore origin $PATH and $PYTHONPATH values, and clean up environment variables that are no longer needed
    export PATH=${ORIG_PATH}
    export PYTHONPATH=${ORIG_PYTHONPATH}
    unset EB_TMPDIR ORIG_PATH ORIG_PYTHONPATH

    module --ignore_cache avail EESSI-extend/${EESSI_EXTEND_VERSION} &> ${ml_av_eessi_extend_out}
    if [[ $? -eq 0 ]]; then
        echo_green ">> EESSI-extend/${EESSI_EXTEND_VERSION} module installed!"
    else
        fatal_error "EESSI-extend/${EESSI_EXTEND_VERSION} module failed to install?! (output of 'pip install' in ${pip_install_out}, output of 'eb' in ${eb_install_out}, output of 'module avail EESSI-extend' in ${ml_av_eessi_extend_out})"
    fi
fi

echo ">> Loading EESSI-extend/${EESSI_EXTEND_VERSION} module..."
module --ignore_cache load EESSI-extend/${EESSI_EXTEND_VERSION}

unset EESSI_EXTEND_VERSION
