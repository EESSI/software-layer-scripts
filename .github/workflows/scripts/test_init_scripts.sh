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

# initialize assert framework
if [ ! -d assert.sh ]; then
  echo "assert.sh not cloned."
  echo ""
  echo "run \`git clone https://github.com/lehmannro/assert.sh.git\`"
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
    ### EXCEPTION FOR CSH ###
    echo -e "\033[33mWe don't now how to test the shell '$shell', PRs are Welcome.\033[0m" 
  else
    # TEST 1: Source Script and check Module Output
    expected="Module for EESSI/$EESSI_VERSION loaded successfully"
    assert "$shell -c 'source init/lmod/$shell' 2>&1 " "${expected}"

    # TEST 2: Check if module overviews first section is the loaded EESSI module
    if [ "$shell" = "csh" ]; then
      # module is defined as alias, but aliases are only retained in interactive
      # shells we work around this by creating a .cshrc file (which sources the
      # init script), and then simply run the remaining commands
      echo "source init/lmod/$shell" > ~/.cshrc
      MODULE_SECTIONS=($($shell -c "module ov" 2>&1 | grep -e '---'))
    else
      MODULE_SECTIONS=($($shell -c "source init/lmod/$shell 2>/dev/null; module ov 2>&1 | grep -e '---'"))
    fi
    PATTERN="/cvmfs/software\.eessi\.io/versions/$EESSI_VERSION/software/linux/x86_64/(intel/haswell|amd/zen3)/modules/all"
    assert_raises 'echo "${MODULE_SECTIONS[1]}" | grep -E "$PATTERN"'

    # TEST 3: Check if module overviews second section is the EESSI init module
    assert "echo ${MODULE_SECTIONS[4]}" "/cvmfs/software.eessi.io/versions/$EESSI_VERSION/init/modules"

    # TEST 4: Load EasyBuild module and check version
    # eb --version outputs: "This is EasyBuild 5.1.1 (framework: 5.1.1, easyblocks: 5.1.1) on host ..."
    if [ "$shell" = "csh" ]; then
      echo "source init/lmod/$shell" > ~/.cshrc
      command="$shell -c 'module load EasyBuild/${EXPECTED_EASYBUILD_VERSION}; eb --version' | cut -d \" \" -f4"
    else
      command="$shell -c 'source init/lmod/$shell 2>/dev/null; module load EasyBuild/${EXPECTED_EASYBUILD_VERSION}; eb --version | cut -d \" \" -f4'"
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
    PATTERN="/cvmfs/software\.eessi\.io/versions/$EESSI_VERSION/software/linux/x86_64/(intel/haswell|amd/zen3)/software/EasyBuild/${EXPECTED_EASYBUILD_VERSION//./\\.}/bin/eb"
    echo "$EASYBUILD_PATH" | grep -E "$PATTERN"
    assert_raises 'echo "$EASYBUILD_PATH" | grep -E "$PATTERN"'

    # End Test Suite
    assert_end "source_eessi_$shell"
  fi
done

# RESET PAGER
export LMOD_PAGER=
