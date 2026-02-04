#!/bin/bash
export LMOD_PAGER=cat

if [ -z ${EESSI_VERSION} ] || [ ! -d /cvmfs/software.eessi.io/versions/${EESSI_VERSION} ]; then
  echo "\$EESSI_VERSION has to be set to a valid EESSI version."
  exit 1
fi

if [ -z ${EXPECTED_EASYBUILD_VERSION} ]; then
  echo "\$EXPECTED_EASYBUILD_VERSION has to be set to an EasyBuild version that is expected to be available in EESSI version ${EESSI_VERSION}."
  exit 1
fi

if [ -z ${EESSI_SOFTWARE_SUBDIR_OVERRIDE} ]; then
  echo "\$EESSI_SOFTWARE_SUBDIR_OVERRIDE has to be set (e.g., x86_64/intel/haswell) so we can do well defined string comparison for the architecture."
  exit 1
fi

# initialize assert framework
if [ ! -d assert.sh ]; then
  echo "assert.sh not cloned."
  echo ""
  echo "run \`git clone https://github.com/lehmannro/assert.sh.git\`"
  echo "(see workflow file that calls this script for how to only clone specific commit if you are worried about security)"
  exit 1
fi
. assert.sh/assert.sh

TEST_SHELLS=("bash" "zsh" "fish" "ksh" "csh")
SHELLS=$@

for shell in ${SHELLS[@]}; do
  echo = | awk 'NF += (OFS = $_) + 100'
  echo  RUNNING TESTS FOR SHELL: $shell
  echo = | awk 'NF += (OFS = $_) + 100'
  if [[ ! " ${TEST_SHELLS[*]} " =~ [[:space:]]${shell}[[:space:]] ]]; then
    echo -e "\033[33mWe don't now how to test the shell '$shell', PRs are Welcome.\033[0m" 
  else
    if [ "$shell" = "csh" ]; then
      # make sure our .cshrc is empty before we begin as we will clobber it
      [ -f ~/.cshrc ] && mv ~/.cshrc ~/.cshrc_orig
    fi

    # TEST 1: Source Script and check Module Output
    expected_pattern=".*EESSI has selected $EESSI_SOFTWARE_SUBDIR_OVERRIDE as the compatible CPU target for EESSI/$EESSI_VERSION.*"
    assert_raises "$shell -c 'source init/lmod/$shell' 2>&1 | grep -E \"${expected_pattern}\""

    # TEST 2: Check if module overviews first section is the loaded EESSI module
    if [ "$shell" = "csh" ]; then
      # module is defined as alias, but aliases are only retained in interactive
      # shells we work around this by creating a .cshrc file (which sources the
      # init script), and then simply run the remaining commands
      echo "source init/lmod/$shell" > ~/.cshrc
      MODULE_SECTIONS=($($shell -c "module ov" 2>&1 | grep -e '---'))
    else
      MODULE_SECTIONS=($($shell -c "source init/lmod/$shell >/dev/null 2>&1; module ov 2>&1 | grep -e '---'"))
    fi
    PATTERN="/cvmfs/software\.eessi\.io/versions/$EESSI_VERSION/software/linux/$EESSI_SOFTWARE_SUBDIR_OVERRIDE/modules/all"
    assert_raises 'echo "${MODULE_SECTIONS[1]}" | grep -E "$PATTERN"'
    # echo "${MODULE_SECTIONS[1]}" "$PATTERN"

    # TEST 3: Check if module overviews second section is the EESSI init module
    assert "echo ${MODULE_SECTIONS[4]}" "/cvmfs/software.eessi.io/init/modules"

    # TEST 4: Load EasyBuild module and check version
    # eb --version outputs: "This is EasyBuild 5.1.1 (framework: 5.1.1, easyblocks: 5.1.1) on host ..."
    if [ "$shell" = "csh" ]; then
      echo "source init/lmod/$shell" > ~/.cshrc
      command="$shell -c 'module load EasyBuild/${EXPECTED_EASYBUILD_VERSION}; eb --version' | tail -n 1 | awk '{print \$4}'"
    else
      command="$shell -c 'source init/lmod/$shell >/dev/null 2>&1; module load EasyBuild/${EXPECTED_EASYBUILD_VERSION}; eb --version' | tail -n 1 | awk '{print \$4}'"
    fi
    assert "$command" "$EXPECTED_EASYBUILD_VERSION"

    # TEST 5: Load EasyBuild module and check path
    if [ "$shell" = "csh" ]; then
      echo "source init/lmod/$shell" > ~/.cshrc
      EASYBUILD_PATH=$($shell -c "module load EasyBuild/${EXPECTED_EASYBUILD_VERSION}; which eb")
    else
      EASYBUILD_PATH=$($shell -c "source init/lmod/$shell 2>/dev/null; module load EasyBuild/${EXPECTED_EASYBUILD_VERSION}; which eb")
    fi
    # escape the dots in ${EASYBUILD_VERSION}
    PATTERN="/cvmfs/software\.eessi\.io/versions/$EESSI_VERSION/software/linux/$EESSI_SOFTWARE_SUBDIR_OVERRIDE/software/EasyBuild/${EXPECTED_EASYBUILD_VERSION//./\\.}/bin/eb"
    # echo "$EASYBUILD_PATH" | grep -E "$PATTERN"
    assert_raises 'echo "$EASYBUILD_PATH" | grep -E "$PATTERN"'
    # echo "$EASYBUILD_PATH" "$PATTERN"

    # TEST 6 and 7: Check the various options (EESSI_DEFAULT_MODULES_APPEND, EESSI_DEFAULT_MODULES_APPEND, EESSI_EXTRA_MODULEPATH) all work
    if [ "$shell" = "csh" ]; then
      echo "setenv EESSI_DEFAULT_MODULES_APPEND append_module" > ~/.cshrc
      echo "setenv EESSI_DEFAULT_MODULES_PREPEND prepend_module" >> ~/.cshrc
      echo "setenv EESSI_EXTRA_MODULEPATH .github/workflows/modules" >> ~/.cshrc
      echo "source init/lmod/$shell" >> ~/.cshrc
      TEST_LMOD_SYSTEM_DEFAULT_MODULES=$($shell -c 'echo $LMOD_SYSTEM_DEFAULT_MODULES')
      TEST_MODULEPATH=$($shell -c 'echo $MODULEPATH')
    elif [ "$shell" = "fish" ]; then
      TEST_LMOD_SYSTEM_DEFAULT_MODULES=$($shell -c 'set -x EESSI_DEFAULT_MODULES_APPEND append_module ; set -x EESSI_DEFAULT_MODULES_PREPEND prepend_module ; set -x EESSI_EXTRA_MODULEPATH .github/workflows/modules ; source init/lmod/'"$shell"' 2>/dev/null; echo $LMOD_SYSTEM_DEFAULT_MODULES')
      TEST_MODULEPATH=$($shell -c 'set -x EESSI_DEFAULT_MODULES_APPEND append_module ; set -x EESSI_DEFAULT_MODULES_PREPEND prepend_module ; set -x EESSI_EXTRA_MODULEPATH .github/workflows/modules ; source init/lmod/'"$shell"' 2>/dev/null; echo $MODULEPATH')
    else
      TEST_LMOD_SYSTEM_DEFAULT_MODULES=$($shell -c 'export EESSI_DEFAULT_MODULES_APPEND=append_module ; export EESSI_DEFAULT_MODULES_PREPEND=prepend_module ; export EESSI_EXTRA_MODULEPATH=.github/workflows/modules ; source init/lmod/'"$shell"' ; echo $LMOD_SYSTEM_DEFAULT_MODULES')
      TEST_MODULEPATH=$($shell -c 'export EESSI_DEFAULT_MODULES_APPEND=append_module ; export EESSI_DEFAULT_MODULES_PREPEND=prepend_module ; export EESSI_EXTRA_MODULEPATH=.github/workflows/modules ; source init/lmod/'"$shell"' 2>/dev/null; echo $MODULEPATH')
    fi
    LMOD_SYSTEM_DEFAULT_MODULES_PATTERN='^prepend_module:.*:append_module$'
    # echo "$TEST_LMOD_SYSTEM_DEFAULT_MODULES" AND "$LMOD_SYSTEM_DEFAULT_MODULES_PATTERN"
    assert_raises 'echo "$TEST_LMOD_SYSTEM_DEFAULT_MODULES" | grep -E "$LMOD_SYSTEM_DEFAULT_MODULES_PATTERN"'
    if [ "$shell" = "fish" ]; then
      MODULEPATH_PATTERN='\.github/workflows/modules$'
    else
      MODULEPATH_PATTERN=':\.github/workflows/modules$'
    fi
    # echo "$TEST_MODULEPATH" AND "$MODULEPATH_PATTERN"
    assert_raises 'echo "$TEST_MODULEPATH" | grep -E "$MODULEPATH_PATTERN"'

    # TEST 8 and 9: Add a conditional test depending on whether we have the Lmod command is available locally or not (Ubuntu-based location for CI)
    if [ -d "$LMOD_PKG/init" ]; then
        echo "Running check for locally available Lmod with purge"
        if [ "$shell" = "csh" ]; then
          echo "source $LMOD_PKG/init/$shell" > ~/.cshrc
          echo "source init/lmod/$shell" >> ~/.cshrc
          TEST_EESSI_WITH_PURGE=$($shell -c 'echo')
          echo "source $LMOD_PKG/init/$shell" > ~/.cshrc
          echo "setenv EESSI_NO_MODULE_PURGE_ON_INIT 1" >> ~/.cshrc
          echo "source init/lmod/$shell" >> ~/.cshrc
          TEST_EESSI_WITHOUT_PURGE=$($shell -c 'echo $EESSI_NO_MODULE_PURGE_ON_INIT')
        elif [ "$shell" = "fish" ]; then
          TEST_EESSI_WITH_PURGE=$($shell -c "source $LMOD_PKG/init/$shell 2>/dev/null ; source init/lmod/$shell 2>/dev/null")
          TEST_EESSI_WITHOUT_PURGE=$($shell -c "set -x EESSI_NO_MODULE_PURGE_ON_INIT 1 ; source $LMOD_PKG/init/$shell 2>/dev/null ; source init/lmod/$shell 2>/dev/null")
        else
          TEST_EESSI_WITH_PURGE=$($shell -c "source $LMOD_PKG/init/$shell 2>/dev/null ; source init/lmod/$shell 2>/dev/null")
          TEST_EESSI_WITHOUT_PURGE=$($shell -c "export EESSI_NO_MODULE_PURGE_ON_INIT=1 ; source $LMOD_PKG/init/$shell 2>/dev/null ; source init/lmod/$shell 2>/dev/null")
        fi
        # In the first case we should have the test and in the second case we shouldn't
        pattern="Modules purged before initialising EESSI"
        echo $TEST_EESSI_WITH_PURGE
        assert_raises 'echo "$TEST_EESSI_WITH_PURGE" | grep "$pattern"'
        # this case should raise 1
        echo $TEST_EESSI_WITHOUT_PURGE
        assert_raises 'echo "$TEST_EESSI_WITHOUT_PURGE" | grep "$pattern"' 1
    fi

    # Optional test 10, check if the prompt has been updated
    if [ "$shell" = "bash" ] || [ "$shell" = "ksh" ] || [ "$shell" = "zsh" ]; then
        # Typically this is a non-interactive shell, so manually set PS1 when testing
        TEST_EESSI_PS1_UPDATE=$($shell -c "PS1='$ ' ; source init/lmod/$shell 2>/dev/null ; echo \"\$PS1\"")
        pattern="{EESSI/${EESSI_VERSION}}"
        assert_raises 'echo "$TEST_EESSI_PS1_UPDATE" | grep "$pattern"'
    fi

    # End Test Suite
    assert_end "source_eessi_$shell"

    if [ "$shell" = "csh" ]; then
      # Restore our .cshrc
      [ -f ~/.cshrc_orig ] && mv ~/.cshrc_orig ~/.cshrc
    fi

  fi
done

# RESET PAGER
export LMOD_PAGER=
