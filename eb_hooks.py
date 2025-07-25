# Hooks to customize how EasyBuild installs software in EESSI
# see https://docs.easybuild.io/en/latest/Hooks.html
import ast
import datetime
import glob
import json
import os
import re

import easybuild.tools.environment as env
from easybuild.easyblocks.generic.configuremake import obtain_config_guess
from easybuild.framework.easyconfig.constants import EASYCONFIG_CONSTANTS
from easybuild.framework.easyconfig.easyconfig import get_toolchain_hierarchy
from easybuild.tools import config
from easybuild.tools.build_log import EasyBuildError, print_msg, print_warning
from easybuild.tools.config import build_option, install_path, update_build_option
from easybuild.tools.filetools import apply_regex_substitutions, copy_dir, copy_file, remove_file, symlink, which
from easybuild.tools.run import run_cmd
from easybuild.tools.systemtools import AARCH64, POWER, X86_64, get_cpu_architecture, get_cpu_features
from easybuild.tools.toolchain.compiler import OPTARCH_GENERIC
from easybuild.tools.toolchain.toolchain import is_system_toolchain
from easybuild.tools.version import VERSION as EASYBUILD_VERSION
from easybuild.tools.modules import get_software_root_env_var_name

# prefer importing LooseVersion from easybuild.tools, but fall back to distuils in case EasyBuild <= 4.7.0 is used
try:
    from easybuild.tools import LooseVersion
except ImportError:
    from distutils.version import LooseVersion


CPU_TARGET_NEOVERSE_N1 = 'aarch64/neoverse_n1'
CPU_TARGET_NEOVERSE_V1 = 'aarch64/neoverse_v1'
CPU_TARGET_AARCH64_GENERIC = 'aarch64/generic'
CPU_TARGET_A64FX = 'aarch64/a64fx'
CPU_TARGET_NVIDIA_GRACE = 'aarch64/nvidia/grace'

CPU_TARGET_CASCADELAKE = 'x86_64/intel/cascadelake'
CPU_TARGET_ICELAKE = 'x86_64/intel/icelake'
CPU_TARGET_SAPPHIRE_RAPIDS = 'x86_64/intel/sapphirerapids'
CPU_TARGET_ZEN4 = 'x86_64/amd/zen4'

EESSI_RPATH_OVERRIDE_ATTR = 'orig_rpath_override_dirs'
EESSI_MODULE_ONLY_ATTR = 'orig_module_only'
EESSI_FORCE_ATTR = 'orig_force'

SYSTEM = EASYCONFIG_CONSTANTS['SYSTEM'][0]

EESSI_INSTALLATION_REGEX = r"^/cvmfs/[^/]*.eessi.io/versions/"
HOST_INJECTIONS_LOCATION = "/cvmfs/software.eessi.io/host_injections/"

# Make sure a single environment variable name is used for this throughout the hooks
EESSI_IGNORE_ZEN4_GCC1220_ENVVAR="EESSI_IGNORE_LMOD_ERROR_ZEN4_GCC1220"

STACK_REPROD_SUBDIR = 'reprod'

EESSI_SUPPORTED_TOP_LEVEL_TOOLCHAINS = {
    '2023.06': [
        {'name': 'foss', 'version': '2022b'},
        {'name': 'foss', 'version': '2023a'},
        {'name': 'foss', 'version': '2023b'},
    ],
    '2025.06': [
        {'name': 'foss', 'version': '2024a'},
        {'name': 'foss', 'version': '2025a'},
    ],
}


def is_gcccore_1220_based(**kwargs):
# ecname, ecversion, tcname, tcversion):
    """
    Checks if this easyconfig either _is_ or _uses_ a GCCcore-12.2.0 based toolchain.
    This function is, for example, used to generate errors in GCCcore-12.2.0 based modules for the zen4 architecture
    since zen4 is not fully supported with that toolchain.

    :param str ecname: Name of the software specified in the EasyConfig
    :param str ecversion: Version of the software specified in the EasyConfig
    :param str tcname: Toolchain name specified in the EasyConfig
    :param str tcversion: Toolchain version specified in the EasyConfig
    """
    ecname = kwargs.get('ecname', None)
    ecversion = kwargs.get('ecversion', None)
    tcname = kwargs.get('tcname', None)
    tcversion = kwargs.get('tcversion', None)

    gcccore_based_names = ['GCCcore', 'GCC']
    foss_based_names = ['gfbf', 'gompi', 'foss']
    return (
        (tcname in foss_based_names and tcversion == '2022b') or
        (tcname in gcccore_based_names and LooseVersion(tcversion) == LooseVersion('12.2.0')) or
        (ecname in foss_based_names and ecversion == '2022b') or
        (ecname in gcccore_based_names and LooseVersion(ecversion) == LooseVersion('12.2.0'))
    )


def get_eessi_envvar(eessi_envvar):
    """Get an EESSI environment variable from the environment"""

    eessi_envvar_value = os.getenv(eessi_envvar)
    if eessi_envvar_value is None:
        raise EasyBuildError("$%s is not defined!", eessi_envvar)

    return eessi_envvar_value


def get_rpath_override_dirs(software_name):
    # determine path to installations in software layer via $EESSI_SOFTWARE_PATH
    eessi_software_path = get_eessi_envvar('EESSI_SOFTWARE_PATH')

    # construct the rpath override directory stub
    rpath_injection_stub = os.path.join(
        # Make sure we are looking inside the `host_injections` directory
        eessi_software_path.replace('versions', 'host_injections', 1),
        # Add the subdirectory for the specific software
        'rpath_overrides',
        software_name,
        # We can't know the version, but this allows the use of a symlink
        # to facilitate version upgrades without removing files
        'system',
    )

    # Allow for libraries in lib or lib64
    rpath_injection_dirs = [os.path.join(rpath_injection_stub, x) for x in ('lib', 'lib64')]

    return rpath_injection_dirs


def parse_hook(ec, *args, **kwargs):
    """Main parse hook: trigger custom functions based on software name."""

    # determine path to Prefix installation in compat layer via $EPREFIX
    eprefix = get_eessi_envvar('EPREFIX')

    if ec.name in PARSE_HOOKS:
        PARSE_HOOKS[ec.name](ec, eprefix)

    # Always trigger this one, regardless of ec.name
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if cpu_target == CPU_TARGET_ZEN4:
        parse_hook_zen4_module_only(ec, eprefix)

    # inject the GPU property (if required)
    ec = inject_gpu_property(ec)


def parse_list_of_dicts_env(var_name):
    """Parse a list of dicts that are stored in an environment variable string"""

    # Check if the environment variable name is valid (letters, numbers, underscores, and doesn't start with a digit)
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', var_name):
        raise ValueError(f"Invalid environment variable name: {var_name}")
    list_string = os.getenv(var_name, '[]')
    
    list_of_dicts = []
    try:
        # Try JSON format first
        list_of_dicts = json.loads(list_string)
    except json.JSONDecodeError:
        try:
            # Fall back to Python literal format
            list_of_dicts = ast.literal_eval(list_string)
        except (ValueError, SyntaxError):
            raise ValueError(f"Environment variable '{var_name}' does not contain a valid list of dictionaries.")
    
    return list_of_dicts


def verify_toolchains_supported_by_eessi_version(easyconfigs):
    """Each EESSI version supports a limited set of toolchains, sanity check the easyconfigs for toolchain support."""
    eessi_version = get_eessi_envvar('EESSI_VERSION')
    supported_eessi_toolchains = []
    # Environment variable can't have a '.' so replace by '_'
    site_top_level_toolchains_envvar = 'EESSI_SITE_TOP_LEVEL_TOOLCHAINS_' + eessi_version.replace('.', '_')
    site_top_level_toolchains = parse_list_of_dicts_env(site_top_level_toolchains_envvar)
    for top_level_toolchain in EESSI_SUPPORTED_TOP_LEVEL_TOOLCHAINS[eessi_version] + site_top_level_toolchains:
        supported_eessi_toolchains += get_toolchain_hierarchy(top_level_toolchain)
    for ec in easyconfigs:
        toolchain = ec['ec']['toolchain']
        # if it is a system toolchain or appears in the list, we are all good
        if is_system_toolchain(toolchain['name']):
            continue
        # This check verifies that the toolchain dict is in the list of supported toolchains.
        # It uses <= as there may be other dict entries in the values returned from get_toolchain_hierarchy()
        # but we only care that the toolchain dict (which has 'name' and 'version') appear.
        elif not any(toolchain.items() <= supported.items() for supported in supported_eessi_toolchains):
            raise EasyBuildError(
                f"Toolchain {toolchain} (required by {ec['full_mod_name']}) is not supported in EESSI/{eessi_version}\n"
                f"Supported toolchains are:\n" + "\n".join(sorted("  " + str(tc) for tc in supported_eessi_toolchains))
            )


def pre_build_and_install_loop_hook(easyconfigs):
    """Main pre_build_and_install_loop hook: trigger custom functions before beginning installation loop."""

    # Always check that toolchain supported by the EESSI version (unless overridden)
    if os.getenv("EESSI_OVERRIDE_TOOLCHAIN_CHECK"):
        print_warning("Overriding the check that the toolchains are supported by the EESSI version.")
    else:
        verify_toolchains_supported_by_eessi_version(easyconfigs)


def post_ready_hook(self, *args, **kwargs):
    """
    Post-ready hook: limit parallellism for selected builds based on software name and CPU target.
                     parallelism needs to be limited because some builds require a lot of memory per used core.
    """
    # 'parallel' easyconfig parameter (EB4) or the parallel property (EB5) is set via EasyBlock.set_parallel
    # in ready step based on available cores
    if hasattr(self, 'parallel'):
        parallel = self.parallel
    else:
        parallel = self.cfg['parallel']
    
    if parallel == 1:
        return  # no need to limit if already using 1 core

    # get CPU target
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')

    new_parallel = parallel

    # check if we have limits defined for this software
    if self.name in PARALLELISM_LIMITS:
        limits = PARALLELISM_LIMITS[self.name]

        # first check for CPU-specific limit
        if cpu_target in limits:
            operation_func, operation_args = limits[cpu_target]
            new_parallel = operation_func(parallel, operation_args)
        # then check for generic limit (applies to all CPU targets)
        elif '*' in limits:
            operation_func, operation_args = limits['*']
            new_parallel = operation_func(parallel, operation_args)
        else:
            return  # no applicable limits found

    # check if there's a general limit set for CPU target
    elif cpu_target in PARALLELISM_LIMITS:
        operation_func, operation_args = PARALLELISM_LIMITS[cpu_target]
        new_parallel = operation_func(parallel, operation_args)

    # apply the limit if it's different from current
    if new_parallel != parallel:
        if hasattr(self, 'parallel'):
            self.cfg.parallel = new_parallel
        else:
            self.cfg['parallel'] = new_parallel
        msg = "limiting parallelism to %s (was %s) for %s on %s to avoid out-of-memory failures during building/testing"
        print_msg(msg % (new_parallel, parallel, self.name, cpu_target), log=self.log)


def pre_prepare_hook(self, *args, **kwargs):
    """Main pre-prepare hook: trigger custom functions."""

    # Check if we have an MPI family in the toolchain (returns None if there is not)
    mpi_family = self.toolchain.mpi_family()

    # Inject an RPATH override for MPI (if needed)
    if mpi_family:
        # Get list of override directories
        mpi_rpath_override_dirs = get_rpath_override_dirs(mpi_family)

        # update the relevant option (but keep the original value so we can reset it later)
        if hasattr(self, EESSI_RPATH_OVERRIDE_ATTR):
            raise EasyBuildError("'self' already has attribute %s! Can't use pre_prepare hook.",
                                 EESSI_RPATH_OVERRIDE_ATTR)

        setattr(self, EESSI_RPATH_OVERRIDE_ATTR, build_option('rpath_override_dirs'))
        if getattr(self, EESSI_RPATH_OVERRIDE_ATTR):
            # self.EESSI_RPATH_OVERRIDE_ATTR is (already) a colon separated string, let's make it a list
            orig_rpath_override_dirs = [getattr(self, EESSI_RPATH_OVERRIDE_ATTR)]
            rpath_override_dirs = ':'.join(orig_rpath_override_dirs + mpi_rpath_override_dirs)
        else:
            rpath_override_dirs = ':'.join(mpi_rpath_override_dirs)
        update_build_option('rpath_override_dirs', rpath_override_dirs)
        print_msg("Updated rpath_override_dirs (to allow overriding MPI family %s): %s",
                  mpi_family, rpath_override_dirs)

    if self.name in PRE_PREPARE_HOOKS:
        PRE_PREPARE_HOOKS[self.name](self, *args, **kwargs)

    # Always trigger this one, regardless of ec.name
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if cpu_target == CPU_TARGET_ZEN4:
        pre_prepare_hook_ignore_zen4_gcccore1220_error(self, *args, **kwargs)


def post_prepare_hook_gcc_prefixed_ld_rpath_wrapper(self, *args, **kwargs):
    """
    Post-configure hook for GCCcore:
    - copy RPATH wrapper script for linker commands to also have a wrapper in
      place with system type prefix like 'x86_64-pc-linux-gnu'
    """
    if self.name == 'GCCcore':
        config_guess = obtain_config_guess()
        system_type, _ = run_cmd(config_guess, log_all=True)
        cmd_prefix = '%s-' % system_type.strip()
        for cmd in ('ld', 'ld.gold', 'ld.bfd'):
            wrapper = which(cmd)
            self.log.info("Path to %s wrapper: %s" % (cmd, wrapper))
            wrapper_dir = os.path.dirname(wrapper)
            prefix_wrapper = os.path.join(wrapper_dir, cmd_prefix + cmd)
            copy_file(wrapper, prefix_wrapper)
            self.log.info("Path to %s wrapper with '%s' prefix: %s" % (cmd, cmd_prefix, which(prefix_wrapper)))

            # we need to tweak the copied wrapper script, so that:
            regex_subs = [
                # - CMD in the script is set to the command name without prefix, because EasyBuild's rpath_args.py
                #   script that is used by the wrapper script only checks for 'ld', 'ld.gold', etc.
                #   when checking whether or not to use -Wl
                ('^CMD=.*', 'CMD=%s' % cmd),
                # - the path to the correct actual binary is logged and called
                ('/%s ' % cmd, '/%s ' % (cmd_prefix + cmd)),
            ]
            apply_regex_substitutions(prefix_wrapper, regex_subs)
    else:
        raise EasyBuildError("GCCcore-specific hook triggered for non-GCCcore easyconfig?!")


def post_prepare_hook(self, *args, **kwargs):
    """Main post-prepare hook: trigger custom functions."""

    if hasattr(self, EESSI_RPATH_OVERRIDE_ATTR):
        # Reset the value of 'rpath_override_dirs' now that we are finished with it
        update_build_option('rpath_override_dirs', getattr(self, EESSI_RPATH_OVERRIDE_ATTR))
        print_msg("Resetting rpath_override_dirs to original value: %s", getattr(self, EESSI_RPATH_OVERRIDE_ATTR))
        delattr(self, EESSI_RPATH_OVERRIDE_ATTR)

    if self.name in POST_PREPARE_HOOKS:
        POST_PREPARE_HOOKS[self.name](self, *args, **kwargs)

    # Always trigger this one, regardless of ec.name
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if cpu_target == CPU_TARGET_ZEN4:
        post_prepare_hook_ignore_zen4_gcccore1220_error(self, *args, **kwargs)


def parse_hook_casacore_disable_vectorize(ec, eprefix):
    """
    Disable 'vectorize' toolchain option for casacore 3.5.0 on aarch64/neoverse_v1
    Compiling casacore 3.5.0 with GCC 13.2.0 (foss-2023b) gives an error when building for aarch64/neoverse_v1.
    See also, https://github.com/EESSI/software-layer/pull/479
    """
    if ec.name == 'casacore':
        tcname, tcversion = ec['toolchain']['name'], ec['toolchain']['version']
        if (
            LooseVersion(ec.version) == LooseVersion('3.5.0') and
            tcname == 'foss' and tcversion == '2023b'
        ):
            cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
            if cpu_target == CPU_TARGET_NEOVERSE_V1:
                # Make sure the toolchainopts key exists, and the value is a dict,
                # before we add the option to disable vectorization
                if 'toolchainopts' not in ec or ec['toolchainopts'] is None:
                    ec['toolchainopts'] = {}
                ec['toolchainopts']['vectorize'] = False
                print_msg("Changed toochainopts for %s: %s", ec.name, ec['toolchainopts'])
            else:
                print_msg("Not changing option vectorize for %s on non-neoverse_v1", ec.name)
        else:
            print_msg("Not changing option vectorize for %s %s %s", ec.name, ec.version, ec.toolchain)
    else:
        raise EasyBuildError("casacore-specific hook triggered for non-casacore easyconfig?!")


def parse_hook_cgal_toolchainopts_precise(ec, eprefix):
    """Enable 'precise' rather than 'strict' toolchain option for CGAL on POWER."""
    if ec.name == 'CGAL':
        if get_cpu_architecture() == POWER:
            # 'strict' implies '-mieee-fp', which is not supported on POWER
            # see https://github.com/easybuilders/easybuild-framework/issues/2077
            ec['toolchainopts']['strict'] = False
            ec['toolchainopts']['precise'] = True
            print_msg("Tweaked toochainopts for %s: %s", ec.name, ec['toolchainopts'])
    else:
        raise EasyBuildError("CGAL-specific hook triggered for non-CGAL easyconfig?!")


def parse_hook_fontconfig_add_fonts(ec, eprefix):
    """Inject --with-add-fonts configure option for fontconfig."""
    if ec.name == 'fontconfig':
        # make fontconfig aware of fonts included with compat layer
        with_add_fonts = '--with-add-fonts=%s' % os.path.join(eprefix, 'usr', 'share', 'fonts')
        ec.update('configopts', with_add_fonts)
        print_msg("Added '%s' configure option for %s", with_add_fonts, ec.name)
    else:
        raise EasyBuildError("fontconfig-specific hook triggered for non-fontconfig easyconfig?!")


def parse_hook_grpcio_zlib(ec, ecprefix):
    """Adjust preinstallopts to use ZLIB from compat layer."""
    if ec.name == 'grpcio' and ec.version in ['1.57.0']:
        exts_list = ec['exts_list']
        original_preinstallopts = (exts_list[0][2])['preinstallopts']
        original_option = "GRPC_PYTHON_BUILD_SYSTEM_ZLIB=True"
        new_option = "GRPC_PYTHON_BUILD_SYSTEM_ZLIB=False"
        (exts_list[0][2])['preinstallopts'] = original_preinstallopts.replace(original_option, new_option, 1)
        print_msg("Modified the easyconfig to use compat ZLIB with GRPC_PYTHON_BUILD_SYSTEM_ZLIB=False")
    else:
        raise EasyBuildError("grpcio-specific hook triggered for a non-grpcio easyconfig?!")


def parse_hook_openblas_relax_lapack_tests_num_errors(ec, eprefix):
    """Relax number of failing numerical LAPACK tests for aarch64/neoverse_v1 CPU target for OpenBLAS < 0.3.23"""
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if ec.name == 'OpenBLAS':
        if LooseVersion(ec.version) < LooseVersion('0.3.23'):
            # relax maximum number of failed numerical LAPACK tests for aarch64/neoverse_v1 CPU target
            # since the default setting of 150 that works well on other aarch64 targets and x86_64 is a bit too strict
            # See https://github.com/EESSI/software-layer/issues/314
            cfg_option = 'max_failing_lapack_tests_num_errors'
            if cpu_target == CPU_TARGET_NEOVERSE_V1:
                orig_value = ec[cfg_option]
                ec[cfg_option] = 400
                print_msg("Maximum number of failing LAPACK tests with numerical errors for %s relaxed to %s (was %s)",
                          ec.name, ec[cfg_option], orig_value)
            else:
                print_msg("Not changing option %s for %s on non-AARCH64", cfg_option, ec.name)
    else:
        raise EasyBuildError("OpenBLAS-specific hook triggered for non-OpenBLAS easyconfig?!")


def parse_hook_pybind11_replace_catch2(ec, eprefix):
    """
    Replace Catch2 build dependency in pybind11 easyconfigs with one that doesn't use system toolchain.
    cfr. https://github.com/easybuilders/easybuild-easyconfigs/pull/19270
    """
    # this is mainly necessary to avoid that --missing keeps reporting Catch2/2.13.9 is missing,
    # and to avoid that we need to use "--from-pr 19270" for every easyconfigs that (indirectly) depends on pybind11
    if ec.name == 'pybind11' and ec.version in ['2.10.3', '2.11.1']:
        build_deps = ec['builddependencies']
        catch2_build_dep = None
        catch2_name, catch2_version = ('Catch2', '2.13.9')
        for idx, build_dep in enumerate(build_deps):
            if build_dep[0] == catch2_name and build_dep[1] == catch2_version:
                catch2_build_dep = build_dep
                break
        if catch2_build_dep and len(catch2_build_dep) == 4 and catch2_build_dep[3] == SYSTEM:
            build_deps[idx] = (catch2_name, catch2_version)


def parse_hook_qt5_check_qtwebengine_disable(ec, eprefix):
    """
    Disable check for QtWebEngine in Qt5 as workaround for problem with determining glibc version.
    """
    if ec.name == 'Qt5':
        # workaround for glibc version being reported as "UNKNOWN" in Gentoo Prefix environment by EasyBuild v4.7.2,
        # see also https://github.com/easybuilders/easybuild-framework/pull/4290
        ec['check_qtwebengine'] = False
        print_msg("Checking for QtWebEgine in Qt5 installation has been disabled")
    else:
        raise EasyBuildError("Qt5-specific hook triggered for non-Qt5 easyconfig?!")


def parse_hook_ucx_eprefix(ec, eprefix):
    """Make UCX aware of compatibility layer via additional configuration options."""
    if ec.name == 'UCX':
        ec.update('configopts', '--with-sysroot=%s' % eprefix)
        ec.update('configopts', '--with-rdmacm=%s' % os.path.join(eprefix, 'usr'))
        print_msg("Using custom configure options for %s: %s", ec.name, ec['configopts'])
    else:
        raise EasyBuildError("UCX-specific hook triggered for non-UCX easyconfig?!")


def parse_hook_freeimage_aarch64(ec, *args, **kwargs):
    """
    Make sure to build with -fPIC on ARM to avoid
    https://github.com/EESSI/software-layer/pull/736#issuecomment-2373261889
    """
    if ec.name == 'FreeImage' and ec.version in ('3.18.0',):
        if get_eessi_envvar('EESSI_CPU_FAMILY') == 'aarch64':
            # Make sure the toolchainopts key exists, and the value is a dict,
            # before we add the option to enable PIC and disable PNG_ARM_NEON_OPT
            if 'toolchainopts' not in ec or ec['toolchainopts'] is None:
                ec['toolchainopts'] = {}
            ec['toolchainopts']['pic'] = True
            ec['toolchainopts']['extra_cflags'] = '-DPNG_ARM_NEON_OPT=0'
            print_msg("Changed toolchainopts for %s: %s", ec.name, ec['toolchainopts'])


def parse_hook_zen4_module_only(ec, eprefix):
    """
    Use --force --module-only if building a foss-2022b-based EasyConfig for Zen4.
    This toolchain will not be supported on Zen4, so we will generate a modulefile
    and have it print an LmodError.
    """
    if is_gcccore_1220_based(ecname=ec['name'], ecversion=ec['version'], tcname=ec['toolchain']['name'],
                             tcversion=ec['toolchain']['version']):
        env_varname = EESSI_IGNORE_ZEN4_GCC1220_ENVVAR
        # TODO: create a docs page to which we can refer for more info here
        # TODO: then update the link to the known issues page to the _specific_ issue
        # Need to escape the newline character so that the newline character actually ends up in the module file
        # (otherwise, it splits the string, and a 2-line string ends up in the modulefile, resulting in syntax error)
        errmsg = "EasyConfigs using toolchains based on GCCcore-12.2.0 are not supported for the Zen4 architecture.\\n"
        errmsg += "See https://www.eessi.io/docs/known_issues/eessi-<EESSI_VERSION>/#gcc-1220-and-foss-2022b-based-modules-cannot-be-loaded-on-zen4-architecture"
        ec['modluafooter'] = 'if (not os.getenv("%s")) then LmodError("%s") end' % (env_varname, errmsg)


def pre_fetch_hook(self, *args, **kwargs):
    """Main pre fetch hook: trigger custom functions based on software name."""
    if self.name in PRE_FETCH_HOOKS:
        PRE_FETCH_HOOKS[ec.name](self, *args, **kwargs)

    # Always trigger this one, regardless of self.name
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if cpu_target == CPU_TARGET_ZEN4:
        pre_fetch_hook_zen4_gcccore1220(self, *args, **kwargs)

    # Always check the software installation path
    pre_fetch_hook_check_installation_path(self, *args, **kwargs)


# Check the installation path so we verify that accelerator software always gets installed into the correct location
def pre_fetch_hook_check_installation_path(self, *args, **kwargs):
    # When we know the CUDA status, we will need to verify the installation path
    # if we are doing an EESSI or host_injections installation
    accelerator_deps = ['CUDA']
    strict_eessi_installation = (
        bool(re.search(EESSI_INSTALLATION_REGEX, self.installdir)) or
        self.installdir.startswith(HOST_INJECTIONS_LOCATION))
    if strict_eessi_installation:
        dependency_names = self.cfg.dependency_names()
        if self.cfg.name in accelerator_deps or any(dep in dependency_names for dep in accelerator_deps):
            # Make sure the path is an accelerator location
            if "/accel/" not in self.installdir:
                raise EasyBuildError(
                    f"It seems you are trying to install an accelerator package {self.cfg.name} into a "
                    f"non-accelerator location {self.installdir}. You need to reconfigure your installation to target "
                    "the correct location."
                    )
        else:
            # If we don't have an accelerator dependency then we should be in a CPU installation path
            if "/accel/" in self.installdir:
                raise EasyBuildError(
                    f"It seems you are trying to install a CPU-only package {self.cfg.name} into accelerator location "
                    f"{self.installdir}. If this is a dependency of the package you are really interested in you will "
                    "need to first install the CPU-only dependencies of that package."
                    )


def pre_fetch_hook_zen4_gcccore1220(self, *args, **kwargs):
    """Use --force --module-only if building a foss-2022b-based EasyConfig for Zen4.
    This toolchain will not be supported on Zen4, so we will generate a modulefile
    and have it print an LmodError.
    """
    if is_gcccore_1220_based(ecname=self.name, ecversion=self.version, tcname=self.toolchain.name,
                             tcversion=self.toolchain.version):
        if hasattr(self, EESSI_MODULE_ONLY_ATTR):
            raise EasyBuildError("'self' already has attribute %s! Can't use pre_fetch hook.",
                                 EESSI_MODULE_ONLY_ATTR)
        setattr(self, EESSI_MODULE_ONLY_ATTR, build_option('module_only'))
        update_build_option('module_only', 'True')
        print_msg("Updated build option 'module-only' to 'True'")

        if hasattr(self, EESSI_FORCE_ATTR):
            raise EasyBuildError("'self' already has attribute %s! Can't use pre_fetch hook.",
                                 EESSI_FORCE_ATTR)
        setattr(self, EESSI_FORCE_ATTR, build_option('force'))
        update_build_option('force', 'True')
        print_msg("Updated build option 'force' to 'True'")


def pre_module_hook_zen4_gcccore1220(self, *args, **kwargs):
    """Make module load-able during module step"""
    if is_gcccore_1220_based(ecname=self.name, ecversion=self.version, tcname=self.toolchain.name,
                             tcversion=self.toolchain.version):
        if hasattr(self, 'initial_environ'):
            # Allow the module to be loaded in the module step (which uses initial environment)
            print_msg(f"Setting {EESSI_IGNORE_ZEN4_GCC1220_ENVVAR} in initial environment")
            self.initial_environ[EESSI_IGNORE_ZEN4_GCC1220_ENVVAR] = "1"


def post_module_hook_zen4_gcccore1220(self, *args, **kwargs):
    """Revert changes from pre_fetch_hook_zen4_gcccore1220"""
    if is_gcccore_1220_based(ecname=self.name, ecversion=self.version, tcname=self.toolchain.name,
                             tcversion=self.toolchain.version):
        if hasattr(self, EESSI_MODULE_ONLY_ATTR):
            update_build_option('module_only', getattr(self, EESSI_MODULE_ONLY_ATTR))
            print_msg("Restored original build option 'module_only' to %s" % getattr(self, EESSI_MODULE_ONLY_ATTR))
        else:
            raise EasyBuildError("Cannot restore module_only to it's original value: 'self' is missing attribute %s.",
                                 EESSI_MODULE_ONLY_ATTR)

        if hasattr(self, EESSI_FORCE_ATTR):
            update_build_option('force', getattr(self, EESSI_FORCE_ATTR))
            print_msg("Restored original build option 'force' to %s" % getattr(self, EESSI_FORCE_ATTR))
        else:
            raise EasyBuildError("Cannot restore force to it's original value: 'self' is misisng attribute %s.",
                                 EESSI_FORCE_ATTR)

        # If the variable to allow loading is set, remove it
        if hasattr(self, 'initial_environ'):
            if self.initial_environ.get(EESSI_IGNORE_ZEN4_GCC1220_ENVVAR, False):
                print_msg(f"Removing {EESSI_IGNORE_ZEN4_GCC1220_ENVVAR} in initial environment")
                del self.initial_environ[EESSI_IGNORE_ZEN4_GCC1220_ENVVAR]


def post_easyblock_hook_copy_easybuild_subdir(self, *args, **kwargs):
    """
    Post easyblock hook that copies the easybuild subdirectory of every installed application
    to a central and timestamped location in the root of the software stack, e.g.:
    /path/to/stack/reprod/MyApp/1.2-foss-2025a/20250102T12:34:56Z
    """

    stack_reprod_dir = os.path.join(os.path.dirname(install_path()), STACK_REPROD_SUBDIR)
    now_utc_timestamp = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S%Z')
    app_easybuild_dir = os.path.join(self.installdir, config.log_path(ec=self.cfg))
    app_reprod_dir = os.path.join(stack_reprod_dir, self.install_subdir, now_utc_timestamp, 'easybuild')
    copy_dir(app_easybuild_dir, app_reprod_dir)


# Modules for dependencies are loaded in the prepare step. Thus, that's where we need this variable to be set
# so that the modules can be succesfully loaded without printing the error (so that we can create a module
# _with_ the warning for the current software being installed)
def pre_prepare_hook_ignore_zen4_gcccore1220_error(self, *args, **kwargs):
    """Set environment variable to ignore the LmodError from parse_hook_zen4_module_only during build phase"""
    if is_gcccore_1220_based(ecname=self.name, ecversion=self.version, tcname=self.toolchain.name,
                             tcversion=self.toolchain.version):
        os.environ[EESSI_IGNORE_ZEN4_GCC1220_ENVVAR] = "1"


def post_prepare_hook_ignore_zen4_gcccore1220_error(self, *args, **kwargs):
    """Unset environment variable to ignore the LmodError from parse_hook_zen4_module_only during build phase"""
    if is_gcccore_1220_based(ecname=self.name, ecversion=self.version, tcname=self.toolchain.name,
                             tcversion=self.toolchain.version):
        del os.environ[EESSI_IGNORE_ZEN4_GCC1220_ENVVAR]


def pre_prepare_hook_highway_handle_test_compilation_issues(self, *args, **kwargs):
    """
    Solve issues with compiling or running the tests on both
    neoverse_n1 and neoverse_v1 with Highway 1.0.4 and GCC 12.3.0:
      - for neoverse_n1 we set optarch to GENERIC
      - for neoverse_v1 and a64fx we completely disable the tests
    cfr. https://github.com/EESSI/software-layer/issues/469
    """
    if self.name == 'Highway':
        tcname, tcversion = self.toolchain.name, self.toolchain.version
        cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
        # note: keep condition in sync with the one used in
        # post_prepare_hook_highway_handle_test_compilation_issues
        if self.version in ['1.0.4'] and tcname == 'GCCcore' and tcversion == '12.3.0':
            if cpu_target in [CPU_TARGET_A64FX, CPU_TARGET_NEOVERSE_V1, CPU_TARGET_NVIDIA_GRACE]:
                self.cfg.update('configopts', '-DHWY_ENABLE_TESTS=OFF')
            if cpu_target == CPU_TARGET_NEOVERSE_N1:
                self.orig_optarch = build_option('optarch')
                update_build_option('optarch', OPTARCH_GENERIC)
    else:
        raise EasyBuildError("Highway-specific hook triggered for non-Highway easyconfig?!")


def post_prepare_hook_highway_handle_test_compilation_issues(self, *args, **kwargs):
    """
    Post-prepare hook for Highway to reset optarch build option.
    """
    if self.name == 'Highway':
        tcname, tcversion = self.toolchain.name, self.toolchain.version
        cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
        # note: keep condition in sync with the one used in
        # pre_prepare_hook_highway_handle_test_compilation_issues
        if self.version in ['1.0.4'] and tcname == 'GCCcore' and tcversion == '12.3.0':
            if cpu_target == CPU_TARGET_NEOVERSE_N1:
                update_build_option('optarch', self.orig_optarch)


def pre_configure_hook(self, *args, **kwargs):
    """Main pre-configure hook: trigger custom functions based on software name."""
    if self.name in PRE_CONFIGURE_HOOKS:
        PRE_CONFIGURE_HOOKS[self.name](self, *args, **kwargs)


def pre_configure_hook_BLIS_a64fx(self, *args, **kwargs):
    """
    Pre-configure hook for BLIS when building for A64FX:
    - add -DCACHE_SECTOR_SIZE_READONLY to $CFLAGS for BLIS 0.9.0, cfr. https://github.com/flame/blis/issues/800
    """
    if self.name == 'BLIS':
        cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
        if self.version == '0.9.0' and cpu_target == CPU_TARGET_A64FX:
            # last argument of BLIS' configure command is configuration target (usually 'auto' for auto-detect),
            # specifying of variables should be done before that
            config_opts = self.cfg['configopts'].split(' ')
            cflags_var = 'CFLAGS="$CFLAGS -DCACHE_SECTOR_SIZE_READONLY"'
            config_target = config_opts[-1]
            self.cfg['configopts'] = ' '.join(config_opts[:-1] + [cflags_var, config_target])
    else:
        raise EasyBuildError("BLIS-specific hook triggered for non-BLIS easyconfig?!")


def pre_configure_hook_CUDA_Samples_test_remove(self, *args, **kwargs):
    """skip immaTensorCoreGemm in CUDA-Samples for compute capability 7.0."""
    if self.name == 'CUDA-Samples' and self.version in ['12.1']:
        # Get compute capability from build option
        cuda_caps = build_option('cuda_compute_capabilities')
        # Check if compute capability 7.0 is in the list
        if cuda_caps and '7.0' in cuda_caps:
            print_msg("Applying hook for CUDA-Samples %s with compute capability 7.0", self.version)
            # local_filters is set by the easyblock, remove path to the Makefile instead
            makefile_path = os.path.join(self.start_dir, 'Samples/3_CUDA_Features/immaTensorCoreGemm/Makefile')
            if os.path.exists(makefile_path):
                remove_file(makefile_path)
                print_msg("Removed Makefile at %s to skip immaTensorCoreGemm build", makefile_path)
            else:
                print_msg("Makefile not found at %s", makefile_path)
    else:
        raise EasyBuildError("CUDA-Samples-specific hook triggered for non-CUDA-Samples easyconfig?!")


def pre_configure_hook_score_p(self, *args, **kwargs):
    """
    Pre-configure hook for Score-p
    - specify correct path to binutils (in compat layer)
    """
    if self.name == 'Score-P':

        # determine path to Prefix installation in compat layer via $EPREFIX
        eprefix = get_eessi_envvar('EPREFIX')

        binutils_lib_path_glob_pattern = os.path.join(eprefix, 'usr', 'lib*', 'binutils', '*-linux-gnu', '2.*')
        binutils_lib_path = glob.glob(binutils_lib_path_glob_pattern)
        if len(binutils_lib_path) == 1:
            self.cfg.update('configopts', '--with-libbfd-lib=' + binutils_lib_path[0])
            self.cfg.update('configopts', '--with-libbfd-include=' + os.path.join(binutils_lib_path[0], 'include'))
        else:
            raise EasyBuildError("Failed to isolate path for binutils libraries using %s, got %s",
                                 binutils_lib_path_glob_pattern, binutils_lib_path)

    else:
        raise EasyBuildError("Score-P-specific hook triggered for non-Score-P easyconfig?!")


def pre_configure_hook_vsearch(self, *args, **kwargs):
    """
    Pre-configure hook for VSEARCH
    - Workaround for a Zlib macro being renamed in Gentoo, see https://bugs.gentoo.org/383179 
      (solves "expected initializer before 'OF'" errors)
    """
    if self.name == 'VSEARCH':
        self.cfg.update('configopts', 'CPPFLAGS="-DOF=_Z_OF ${CPPFLAGS}"')
    else:
        raise EasyBuildError("VSEARCH-specific hook triggered for non-VSEARCH easyconfig?!")


def pre_configure_hook_extrae(self, *args, **kwargs):
    """
    Pre-configure hook for Extrae
    - avoid use of 'which' in configure script
    - specify correct path to binutils/zlib (in compat layer)
    """
    if self.name == 'Extrae':

        # determine path to Prefix installation in compat layer via $EPREFIX
        eprefix = get_eessi_envvar('EPREFIX')

        binutils_lib_path_glob_pattern = os.path.join(eprefix, 'usr', 'lib*', 'binutils', '*-linux-gnu', '2.*')
        binutils_lib_path = glob.glob(binutils_lib_path_glob_pattern)
        if len(binutils_lib_path) == 1:
            self.cfg.update('configopts', '--with-binutils=' + binutils_lib_path[0])
        else:
            raise EasyBuildError("Failed to isolate path for binutils libraries using %s, got %s",
                                 binutils_lib_path_glob_pattern, binutils_lib_path)

        # zlib is a filtered dependency, so we need to manually specify it's location to avoid the host version
        self.cfg.update('configopts', '--with-libz=' + eprefix)

        # replace use of 'which' with 'command -v', since 'which' is broken in EESSI build container;
        # this must be done *after* running configure script, because initial configuration re-writes configure script,
        # and problem due to use of which only pops up when running make ?!
        self.cfg.update(
            'prebuildopts',
            "cp config/mpi-macros.m4 config/mpi-macros.m4.orig && "
            "sed -i 's/`which /`command -v /g' config/mpi-macros.m4 && "
            )
    else:
        raise EasyBuildError("Extrae-specific hook triggered for non-Extrae easyconfig?!")


def pre_configure_hook_gobject_introspection(self, *args, **kwargs):
    """
    pre-configure hook for GObject-Introspection:
    - prevent GObject-Introspection from setting $LD_LIBRARY_PATH if EasyBuild is configured to filter it, see:
      https://github.com/EESSI/software-layer/issues/196
    """
    if self.name == 'GObject-Introspection':
        # inject a line that removes all items from runtime_path_envvar that are in $EASYBUILD_FILTER_ENVVARS
        sed_cmd = r'sed -i "s@\(^\s*runtime_path_envvar = \)\(.*\)@'
        sed_cmd += r'\1\2\n\1 [x for x in runtime_path_envvar if not x in os.environ.get(\'EASYBUILD_FILTER_ENV_VARS\', \'\').split(\',\')]@g"'
        sed_cmd += ' %(start_dir)s/giscanner/ccompiler.py && '
        self.cfg.update('preconfigopts', sed_cmd)
    else:
        raise EasyBuildError("GObject-Introspection-specific hook triggered for non-GObject-Introspection easyconfig?!")


def pre_configure_hook_gromacs(self, *args, **kwargs):
    """
    Pre-configure hook for GROMACS:
    - avoid building with SVE instructions on Neoverse V1 as workaround for failing tests,
      see https://gitlab.com/gromacs/gromacs/-/issues/5057 + https://gitlab.com/eessi/support/-/issues/47
    """
    if self.name == 'GROMACS':
        cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
        if (
            (LooseVersion(self.version) <= LooseVersion('2024.1') and cpu_target == CPU_TARGET_NEOVERSE_V1) or
            (LooseVersion(self.version) <= LooseVersion('2024.4') and cpu_target == CPU_TARGET_NVIDIA_GRACE)
        ):
            self.cfg.update('configopts', '-DGMX_SIMD=ARM_NEON_ASIMD')
            print_msg(
                "Avoiding use of SVE instructions for GROMACS %s by using ARM_NEON_ASIMD as GMX_SIMD value",
                self.version
                )
    else:
        raise EasyBuildError("GROMACS-specific hook triggered for non-GROMACS easyconfig?!")


def pre_configure_hook_llvm(self, *args, **kwargs):
    """Adjust internal configure options for the LLVM EasyBlock to reinstate filtered out dependencies.
    In the LLVM EasyBlock, most checks concerning loaded modules are performed at the configure_step.
    The EB uses a global `general_opts` dict to keep track of options that needs to be reused across stages.
    The way the EB is structured does allow to inject a CMAKE option through `self._cfgopts` which is a splitted list
    of the `configure_opts` passed through the EC, but does not allow to override as the `general_opts` dict will
    take precedence over the `self._cfgopts` list.

    We can instead set the environment variable that EasyBuild uses for `get_software_root` to trick the EB into
    into pointing to the compat layer.
    """
    if self.name in ['LLVM', 'ROCm-LLVM']:
        eprefix = get_eessi_envvar('EPREFIX')

        for software in ('zlib', 'ncurses'):
            var_name = get_software_root_env_var_name(software)
            env.setvar(var_name, os.path.join(eprefix, 'usr'))
    else:
        raise EasyBuildError("LLVM-specific hook triggered for non-LLVM easyconfig?!")


def pre_configure_hook_openblas_optarch_generic(self, *args, **kwargs):
    """
    Pre-configure hook for OpenBLAS: add DYNAMIC_ARCH=1 to build/test/install options when using --optarch=GENERIC
    """
    # note: OpenBLAS easyblock was updated in https://github.com/easybuilders/easybuild-easyblocks/pull/3492
    # to take care of this already, so at some point this hook can be removed...
    if self.name == 'OpenBLAS':
        if build_option('optarch') == OPTARCH_GENERIC:
            dynamic_arch = 'DYNAMIC_ARCH=1'
            for step in ('build', 'test', 'install'):
                if dynamic_arch not in self.cfg[f'{step}opts']:
                    self.cfg.update(f'{step}opts', dynamic_arch)

            if get_cpu_architecture() == AARCH64:
                # when building for aarch64/generic, we also need to set TARGET=ARMV8 to make sure
                # that the driver parts of OpenBLAS are compiled generically;
                # see also https://github.com/OpenMathLib/OpenBLAS/issues/4945
                target_armv8 = 'TARGET=ARMV8'
                for step in ('build', 'test', 'install'):
                    if target_armv8 not in self.cfg[f'{step}opts']:
                        self.cfg.update(f'{step}opts', target_armv8)

                # use -mtune=generic rather than -mcpu=generic in $CFLAGS for aarch64/generic,
                # because -mcpu=generic implies a particular -march=armv* which clashes with those used by OpenBLAS
                # when building with DYNAMIC_ARCH=1
                mcpu_generic = '-mcpu=generic'
                cflags = os.getenv('CFLAGS')
                if mcpu_generic in cflags:
                    cflags = cflags.replace(mcpu_generic, '-mtune=generic')
                    self.log.info("Replaced -mcpu=generic with -mtune=generic in $CFLAGS")
                    env.setvar('CFLAGS', cflags)
    else:
        raise EasyBuildError("OpenBLAS-specific hook triggered for non-OpenBLAS easyconfig?!")


def pre_configure_hook_libfabric_disable_psm3_x86_64_generic(self, *args, **kwargs):
    """Add --disable-psm3 to libfabric configure options when building with --optarch=GENERIC on x86_64."""
    if self.name == 'libfabric':
        if get_cpu_architecture() == X86_64:
            generic = build_option('optarch') == OPTARCH_GENERIC
            no_avx = 'avx' not in get_cpu_features()
            if generic or no_avx:
                self.cfg.update('configopts', '--disable-psm3')
                print_msg("Using custom configure options for %s: %s", self.name, self.cfg['configopts'])
    else:
        raise EasyBuildError("libfabric-specific hook triggered for non-libfabric easyconfig?!")


def pre_configure_hook_metabat_filtered_zlib_dep(self, *args, **kwargs):
    """
    Pre-configure hook for MetaBAT:
    - take into account that zlib is a filtered dependency,
      and that there's no libz.a in the EESSI compat layer
    """
    if self.name == 'MetaBAT':
        configopts = self.cfg['configopts']
        regex = re.compile(r"\$EBROOTZLIB/lib/libz.a")
        self.cfg['configopts'] = regex.sub('$EPREFIX/usr/lib64/libz.so', configopts)
    else:
        raise EasyBuildError("MetaBAT-specific hook triggered for non-MetaBAT easyconfig?!")


def pre_configure_hook_wrf_aarch64(self, *args, **kwargs):
    """
    Pre-configure hook for WRF:
    - patch arch/configure_new.defaults so building WRF with foss toolchain works on aarch64
    """
    if self.name == 'WRF':
        if get_cpu_architecture() == AARCH64:
            pattern = "Linux x86_64 ppc64le, gfortran"
            repl = "Linux x86_64 aarch64 ppc64le, gfortran"
            if LooseVersion(self.version) <= LooseVersion('3.9.0'):
                self.cfg.update('preconfigopts', "sed -i 's/%s/%s/g' arch/configure_new.defaults && " % (pattern, repl))
                print_msg("Using custom preconfigopts for %s: %s", self.name, self.cfg['preconfigopts'])

            if LooseVersion('4.0.0') <= LooseVersion(self.version) <= LooseVersion('4.2.1'):
                self.cfg.update('preconfigopts', "sed -i 's/%s/%s/g' arch/configure.defaults && " % (pattern, repl))
                print_msg("Using custom preconfigopts for %s: %s", self.name, self.cfg['preconfigopts'])
    else:
        raise EasyBuildError("WRF-specific hook triggered for non-WRF easyconfig?!")


def pre_configure_hook_LAMMPS_zen4(self, *args, **kwargs):
    """
    pre-configure hook for LAMMPS:
    - set kokkos_arch on x86_64/amd/zen4
    """

    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if self.name == 'LAMMPS':
        if self.version in ('2Aug2023_update2', '2Aug2023_update4', '29Aug2024'):
            if get_cpu_architecture() == X86_64:
                if cpu_target == CPU_TARGET_ZEN4:
                    # There is no support for ZEN4 in LAMMPS yet so falling back to ZEN3
                    self.cfg['kokkos_arch'] = 'ZEN3'
    else:
        raise EasyBuildError("LAMMPS-specific hook triggered for non-LAMMPS easyconfig?!")


def pre_test_hook(self, *args, **kwargs):
    """Main pre-test hook: trigger custom functions based on software name."""
    if self.name in PRE_TEST_HOOKS:
        PRE_TEST_HOOKS[self.name](self, *args, **kwargs)


def pre_test_hook_exclude_failing_test_Highway(self, *args, **kwargs):
    """
    Pre-test hook for Highway: exclude failing TestAllShiftRightLanes/SVE_256 test on neoverse_v1
    cfr. https://github.com/EESSI/software-layer/issues/469
    and exclude failing tests
      HwyReductionTestGroup/HwyReductionTest.TestAllSumOfLanes/SVE2_128
      HwyReductionTestGroup/HwyReductionTest.TestAllSumOfLanes/SVE2
      HwyReductionTestGroup/HwyReductionTest.TestAllSumOfLanes/SVE
    on nvidia/grace
    """
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if self.name == 'Highway' and self.version in ['1.0.3'] and cpu_target == CPU_TARGET_NEOVERSE_V1:
        self.cfg['runtest'] += ' ARGS="-E TestAllShiftRightLanes/SVE_256"'
    if self.name == 'Highway' and self.version in ['1.0.3'] and cpu_target == CPU_TARGET_NVIDIA_GRACE:
        self.cfg['runtest'] += ' ARGS="-E TestAllSumOfLanes"'


def pre_test_hook_ignore_failing_tests_ESPResSo(self, *args, **kwargs):
    """
    Pre-test hook for ESPResSo: skip failing tests, tests frequently timeout due to known bugs in ESPResSo v4.2.1
    cfr. https://github.com/EESSI/software-layer/issues/363
    """
    if self.name == 'ESPResSo' and self.version == '4.2.1':
        self.cfg['testopts'] = "|| echo 'ignoring failing tests (probably due to timeouts)'"


def pre_test_hook_ignore_failing_tests_FFTWMPI(self, *args, **kwargs):
    """
    Pre-test hook for FFTW.MPI: skip failing tests for FFTW.MPI 3.3.10 on neoverse_v1
    cfr. https://github.com/EESSI/software-layer/issues/325
    """
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if self.name == 'FFTW.MPI' and self.version == '3.3.10' and cpu_target == CPU_TARGET_NEOVERSE_V1:
        self.cfg['testopts'] = "|| echo ignoring failing tests"


def pre_test_hook_ignore_failing_tests_SciPybundle(self, *args, **kwargs):
    """
    Pre-test hook for SciPy-bundle: skip failing tests for selected SciPy-bundle versions
    In version 2021.10 on neoverse_v1, 2 failing tests in scipy 1.6.3:
        FAILED optimize/tests/test_linprog.py::TestLinprogIPSparse::test_bug_6139 - A...
        FAILED optimize/tests/test_linprog.py::TestLinprogIPSparsePresolve::test_bug_6139
        = 2 failed, 30554 passed, 2064 skipped, 10992 deselected, 76 xfailed, 7 xpassed, 40 warnings in 380.27s (0:06:20) =
    In versions 2023.02 + 2023.07 + 2023.11 on neoverse_v1, 2 failing tests in scipy (versions 1.10.1, 1.11.1, 1.11.4):
        FAILED scipy/spatial/tests/test_distance.py::TestPdist::test_pdist_correlation_iris
        FAILED scipy/spatial/tests/test_distance.py::TestPdist::test_pdist_correlation_iris_float32
        = 2 failed, 54409 passed, 3016 skipped, 223 xfailed, 13 xpassed, 10917 warnings in 892.04s (0:14:52) =
    In version 2023.07 on a64fx, 4 failing tests in scipy 1.11.1:
        FAILED scipy/optimize/tests/test_linprog.py::TestLinprogIPSparse::test_bug_6139
        FAILED scipy/optimize/tests/test_linprog.py::TestLinprogIPSparsePresolve::test_bug_6139
        FAILED scipy/spatial/tests/test_distance.py::TestPdist::test_pdist_correlation_iris
        FAILED scipy/spatial/tests/test_distance.py::TestPdist::test_pdist_correlation_iris_float32
        = 4 failed, 54407 passed, 3016 skipped, 223 xfailed, 13 xpassed, 10917 warnings in 6068.43s (1:41:08) =
    In version 2023.07 + 2023.11 on grace, 2 failing tests in scipy (versions 1.11.1,  1.11.4):
        FAILED scipy/optimize/tests/test_linprog.py::TestLinprogIPSparse::test_bug_6139
        FAILED scipy/optimize/tests/test_linprog.py::TestLinprogIPSparsePresolve::test_bug_6139
        = 2 failed, 54876 passed, 3021 skipped, 223 xfailed, 13 xpassed in 581.85s (0:09:41) =
    In version 2023.02 on grace, 46 failing tests in scipy (versions 1.10.1):
        FAILED ../../linalg/tests/test_basic.py::TestOverwrite::test_pinv - RuntimeWa...
        FAILED ../../linalg/tests/test_basic.py::TestOverwrite::test_pinvh - RuntimeW...
        FAILED ../../linalg/tests/test_matfuncs.py::TestExpM::test_2x2_input - Runtim...
        FAILED ../../optimize/tests/test_linprog.py::TestLinprogIPSparse::test_bug_6139
        FAILED ../../optimize/tests/test_linprog.py::TestLinprogIPSparsePresolve::test_bug_6139
        FAILED ../../optimize/tests/test_zeros.py::test_gh_9608_preserve_array_shape
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[True-True-True-coo_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[True-True-False-array-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[True-True-False-csr_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[True-True-False-coo_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[False-True-True-array-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[False-True-True-csr_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[False-True-True-coo_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[True-True-True-array-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-True-False-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[False-True-False-array-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[True-True-True-csr_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-True-False-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-True-True-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-True-True-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-True-False-True-float32-coo_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[False-True-False-csr_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-True-True-True-float32-coo_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-True-False-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-False-False-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-False-False-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-False-False-True-float32-coo_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_asymmetric_laplacian[False-True-False-coo_matrix-float32]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-True-False-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-True-False-True-float32-coo_matrix]
        FAILED ../../sparse/linalg/_eigen/lobpcg/tests/test_lobpcg.py::test_tolerance_float32
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-True-True-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-False-False-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-False-False-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-False-False-True-float32-coo_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-True-True-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-True-True-True-float32-coo_matrix]
        FAILED ../../sparse/linalg/_eigen/lobpcg/tests/test_lobpcg.py::test_random_initial_float32
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-False-True-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-False-True-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[function-False-True-True-float32-coo_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-False-True-True-float32-asarray]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-False-True-True-float32-csr_matrix]
        FAILED ../../sparse/csgraph/tests/test_graph_laplacian.py::test_format[lo-False-True-True-float32-coo_matrix]
        FAILED ../../sparse/linalg/_isolve/tests/test_iterative.py::test_precond_dummy
        FAILED ../../sparse/linalg/_eigen/arpack/tests/test_arpack.py::test_symmetric_modes
        = 46 failed, 49971 passed, 2471 skipped, 231 xfailed, 11 xpassed in 65.91s (0:01:05) =
    (in previous versions we were not as strict yet on the numpy/SciPy tests)
    """
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    scipy_bundle_versions_nv1 = ('2021.10', '2023.02', '2023.07', '2023.11')
    scipy_bundle_versions_a64fx = ('2023.07', '2023.11')
    scipy_bundle_versions_nvidia_grace = ('2023.02', '2023.07', '2023.11')
    if self.name == 'SciPy-bundle':
        if cpu_target == CPU_TARGET_NEOVERSE_V1 and self.version in scipy_bundle_versions_nv1:
            self.cfg['testopts'] = "|| echo ignoring failing tests"
        elif cpu_target == CPU_TARGET_A64FX and self.version in scipy_bundle_versions_a64fx:
            self.cfg['testopts'] = "|| echo ignoring failing tests"
        elif cpu_target == CPU_TARGET_NVIDIA_GRACE and self.version in scipy_bundle_versions_nvidia_grace:
            self.cfg['testopts'] = "|| echo ignoring failing tests"


def pre_test_hook_ignore_failing_tests_netCDF(self, *args, **kwargs):
    """
    Pre-test hook for netCDF: skip failing tests for selected netCDF versions on neoverse_v1
    cfr. https://github.com/EESSI/software-layer/issues/425
    The following tests are problematic:
        163 - nc_test4_run_par_test (Timeout)
        190 - h5_test_run_par_tests (Timeout)
    A few other tests are skipped in the easyconfig and patches for similar issues, see above issue for details.
    """
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if self.name == 'netCDF' and self.version == '4.9.2' and cpu_target == CPU_TARGET_NEOVERSE_V1:
        self.cfg['testopts'] = "|| echo ignoring failing tests"


def pre_test_hook_increase_max_failed_tests_arm_PyTorch(self, *args, **kwargs):
    """
    Pre-test hook for PyTorch: increase the number of max failing tests
    See https://github.com/EESSI/software-layer/issues/461
    """
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if self.name == 'PyTorch' and self.version == '2.1.2':
        if get_cpu_architecture() == AARCH64:
            self.cfg['max_failed_tests'] = 10
        if cpu_target in [CPU_TARGET_CASCADELAKE, CPU_TARGET_ICELAKE, CPU_TARGET_SAPPHIRE_RAPIDS]:
            self.cfg['max_failed_tests'] = 4


def pre_single_extension_hook(ext, *args, **kwargs):
    """Main pre-extension: trigger custom functions based on software name."""
    if ext.name in PRE_SINGLE_EXTENSION_HOOKS:
        PRE_SINGLE_EXTENSION_HOOKS[ext.name](ext, *args, **kwargs)


def post_single_extension_hook(ext, *args, **kwargs):
    """Main post-extension hook: trigger custom functions based on software name."""
    if ext.name in POST_SINGLE_EXTENSION_HOOKS:
        POST_SINGLE_EXTENSION_HOOKS[ext.name](ext, *args, **kwargs)


def pre_single_extension_isoband(ext, *args, **kwargs):
    """
    Pre-extension hook for isoband R package, to fix build on top of recent glibc.
    """
    if ext.name == 'isoband' and LooseVersion(ext.version) < LooseVersion('0.2.5'):
        # use constant value instead of SIGSTKSZ for stack size in vendored testthat included in isoband sources,
        # cfr. https://github.com/r-lib/isoband/commit/6984e6ce8d977f06e0b5ff73f5d88e5c9a44c027
        ext.cfg['preinstallopts'] = "sed -i 's/SIGSTKSZ/32768/g' src/testthat/vendor/catch.h && "


def pre_single_extension_numpy(ext, *args, **kwargs):
    """
    Pre-extension hook for numpy, to change -march=native to -march=armv8.4-a for numpy 1.24.2
    when building for aarch64/neoverse_v1 CPU target.
    """
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if ext.name == 'numpy' and ext.version == '1.24.2' and cpu_target == CPU_TARGET_NEOVERSE_V1:
        # note: this hook is called before build environment is set up (by calling toolchain.prepare()),
        # so environment variables like $CFLAGS are not defined yet
        # unsure which of these actually matter for numpy, so changing all of them
        ext.orig_optarch = build_option('optarch')
        update_build_option('optarch', 'march=armv8.4-a')


def post_single_extension_numpy(ext, *args, **kwargs):
    """
    Post-extension hook for numpy, to reset 'optarch' build option.
    """
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if ext.name == 'numpy' and ext.version == '1.24.2' and cpu_target == CPU_TARGET_NEOVERSE_V1:
        update_build_option('optarch', ext.orig_optarch)


def pre_single_extension_testthat(ext, *args, **kwargs):
    """
    Pre-extension hook for testthat R package, to fix build on top of recent glibc.
    """
    if ext.name == 'testthat' and LooseVersion(ext.version) < LooseVersion('3.1.0'):
        # use constant value instead of SIGSTKSZ for stack size,
        # cfr. https://github.com/r-lib/testthat/issues/1373 + https://github.com/r-lib/testthat/pull/1403
        ext.cfg['preinstallopts'] = "sed -i 's/SIGSTKSZ/32768/g' inst/include/testthat/vendor/catch.h && "


def post_postproc_hook(self, *args, **kwargs):
    """Main post-postprocessing hook: trigger custom functions based on software name."""
    if self.name in POST_POSTPROC_HOOKS:
        POST_POSTPROC_HOOKS[self.name](self, *args, **kwargs)


def post_postproc_cuda(self, *args, **kwargs):
    """
    Remove files from CUDA installation that we are not allowed to ship,
    and replace them with a symlink to a corresponding installation under host_injections.
    """
    if self.name == 'CUDA':
        # This hook only acts on an installation under repositories that _we_ ship (*.eessi.io/versions)
        eessi_installation = bool(re.search(EESSI_INSTALLATION_REGEX, self.installdir))

        if eessi_installation:
            print_msg("Replacing files in CUDA installation that we can not ship with symlinks to host_injections...")

            # read CUDA EULA, construct allowlist based on section 2.6 that specifies list of files that can be shipped
            eula_path = os.path.join(self.installdir, 'EULA.txt')
            relevant_eula_lines = []
            with open(eula_path) as infile:
                copy = False
                for line in infile:
                    if line.strip() == "2.6. Attachment A":
                        copy = True
                        continue
                    elif line.strip() == "2.7. Attachment B":
                        copy = False
                        continue
                    elif copy:
                        relevant_eula_lines.append(line)

            # create list without file extensions, they're not really needed and they only complicate things
            allowlist = ['EULA', 'README']
            file_extensions = ['.so', '.a', '.h', '.bc']
            for line in relevant_eula_lines:
                for word in line.split():
                    if any(ext in word for ext in file_extensions):
                        allowlist.append(os.path.splitext(word)[0])
            # The EULA of CUDA 12.4 introduced a typo (confirmed by NVIDIA):
            # libnvrtx-builtins_static.so should be libnvrtc-builtins_static.so
            if 'libnvrtx-builtins_static' in allowlist:
                allowlist.remove('libnvrtx-builtins_static')
                allowlist.append('libnvrtc-builtins_static')
            allowlist = sorted(set(allowlist))
            self.log.info(
                "Allowlist for files in CUDA installation that can be redistributed: " + ', '.join(allowlist)
                )

            # Do some quick sanity checks for things we should or shouldn't have in the list
            if 'nvcc' in allowlist:
                raise EasyBuildError("Found 'nvcc' in allowlist: %s" % allowlist)
            if 'libcudart' not in allowlist:
                raise EasyBuildError("Did not find 'libcudart' in allowlist: %s" % allowlist)

            # replace files that are not distributable with symlinks into
            # host_injections
            replace_non_distributable_files_with_symlinks(self.log, self.installdir, self.name, allowlist)
        else:
            print_msg(f"EESSI hook to respect CUDA license not triggered for installation path {self.installdir}")
    else:
        raise EasyBuildError("CUDA-specific hook triggered for non-CUDA easyconfig?!")


def post_postproc_cudnn(self, *args, **kwargs):
    """
    Remove files from cuDNN installation that we are not allowed to ship,
    and replace them with a symlink to a corresponding installation under host_injections.
    """

    if self.name == 'cuDNN':
        # This hook only acts on an installation under repositories that _we_ ship (*.eessi.io/versions)
        eessi_installation = bool(re.search(EESSI_INSTALLATION_REGEX, self.installdir))

        if eessi_installation:
            print_msg("Replacing files in cuDNN installation that we can not ship with symlinks to host_injections...")

            allowlist = ['LICENSE']

            # read cuDNN LICENSE, construct allowlist based on section "2. Distribution" that specifies list of files that can be shipped
            license_path = os.path.join(self.installdir, 'LICENSE')
            search_string = "2. Distribution. The following portions of the SDK are distributable under the Agreement:"
            found_search_string = False
            with open(license_path) as infile:
                for line in infile:
                    if line.strip().startswith(search_string):
                        found_search_string = True
                        # remove search string, split into words, remove trailing
                        # dots '.' and only retain words starting with a dot '.'
                        distributable = line[len(search_string):]
                        # distributable looks like ' the runtime files .so and .dll.'
                        # note the '.' after '.dll'
                        for word in distributable.split():
                            if word[0] == '.':
                                # rstrip is used to remove the '.' after '.dll'
                                allowlist.append(word.rstrip('.'))
            if not found_search_string:
                # search string wasn't found in LICENSE file
                raise EasyBuildError("search string '%s' was not found in license file '%s';"
                                     "hence installation may be replaced by symlinks only",
                                     search_string, license_path)

            allowlist = sorted(set(allowlist))
            self.log.info("Allowlist for files in cuDNN installation that can be redistributed: " + ', '.join(allowlist))

            # replace files that are not distributable with symlinks into
            # host_injections
            replace_non_distributable_files_with_symlinks(self.log, self.installdir, self.name, allowlist)
        else:
            print_msg(f"EESSI hook to respect cuDDN license not triggered for installation path {self.installdir}")
    else:
        raise EasyBuildError("cuDNN-specific hook triggered for non-cuDNN easyconfig?!")


def replace_non_distributable_files_with_symlinks(log, install_dir, pkg_name, allowlist):
    """
    Replace files that cannot be distributed with symlinks into host_injections
    """
    # Different packages use different ways to specify which files or file
    # 'types' may be redistributed. For CUDA, the 'EULA.txt' lists full file
    # names. For cuDNN, the 'LICENSE' lists file endings/suffixes (e.g., '.so')
    # that can be redistributed.
    # The map 'extension_based' defines which of these two ways are employed. If
    # full file names are used it maps a package name (key) to False (value). If
    # endings/suffixes are used, it maps a package name to True. Later we can
    # easily use this data structure to employ the correct method for
    # postprocessing an installation.
    extension_based = {
        "CUDA": False,
        "cuDNN": True,
    }
    if not pkg_name in extension_based:
        raise EasyBuildError("Don't know how to strip non-distributable files from package %s.", pkg_name)

    # iterate over all files in the package installation directory
    for dir_path, _, files in os.walk(install_dir):
        for filename in files:
            full_path = os.path.join(dir_path, filename)
            # we only really care about real files, i.e. not symlinks
            if not os.path.islink(full_path):
                check_by_extension = extension_based[pkg_name] and '.' in filename
                if check_by_extension:
                    # if the allowlist only contains extensions, we have to
                    # determine that from filename. we assume the extension is
                    # the second element when splitting the filename at dots
                    # (e.g., for 'libcudnn_adv_infer.so.8.9.2' the extension
                    # would be '.so')
                    extension = '.' + filename.split('.')[1]
                # check if the current file name stub or its extension is part of the allowlist
                basename =  filename.split('.')[0]
                if basename in allowlist:
                    log.debug("%s is found in allowlist, so keeping it: %s", basename, full_path)
                elif check_by_extension and extension in allowlist:
                    log.debug("%s is found in allowlist, so keeping it: %s", extension, full_path)
                else:
                    print_name = filename if extension_based[pkg_name] else basename
                    log.debug("%s is not found in allowlist, so replacing it with symlink: %s",
                              print_name, full_path)
                    # the host_injections path is under a fixed repo/location for CUDA or cuDNN
                    host_inj_path = re.sub(EESSI_INSTALLATION_REGEX, HOST_INJECTIONS_LOCATION, full_path)
                    # CUDA and cu* libraries themselves don't care about compute capability so remove this
                    # duplication from under host_injections (symlink to a single CUDA or cu* library
                    # installation for all compute capabilities)
                    accel_subdir = get_eessi_envvar("EESSI_ACCELERATOR_TARGET")
                    if accel_subdir:
                        host_inj_path = host_inj_path.replace("/accel/%s" % accel_subdir, '')
                    # make sure source and target of symlink are not the same
                    if full_path == host_inj_path:
                        raise EasyBuildError("Source (%s) and target (%s) are the same location, are you sure you "
                                             "are using this hook for an EESSI installation?",
                                             full_path, host_inj_path)
                    remove_file(full_path)
                    symlink(host_inj_path, full_path)


def inject_gpu_property(ec):
    """
    Add 'gpu' property and EESSI<PACKAGE>VERSION envvars via modluafooter
    easyconfig parameter, and drop dependencies to build dependencies
    """
    ec_dict = ec.asdict()
    # Check if CUDA, cuDNN, you-name-it is in the dependencies, if so
    # - drop dependency to build dependency
    # - add 'gpu' Lmod property
    # - add envvar with package version
    pkg_names = ( "CUDA", "cuDNN" )
    pkg_versions = { }
    add_gpu_property = ''

    for pkg_name in pkg_names:
        # Check if pkg_name is in the dependencies, if so drop dependency to build
        # dependency and set variable for later adding the 'gpu' Lmod property
        # to '.remove' dependencies from ec_dict['dependencies'] we make a copy,
        # iterate over the copy and can then savely use '.remove' on the original
        # ec_dict['dependencies'].
        deps = ec_dict['dependencies'][:]
        if (pkg_name in [dep[0] for dep in deps]):
            add_gpu_property = 'add_property("arch","gpu")'
            for dep in deps:
                if pkg_name == dep[0]:
                    # make pkg_name a build dependency only (rpathing saves us from link errors)
                    ec.log.info("Dropping dependency on %s to build dependency" % pkg_name)
                    ec_dict['dependencies'].remove(dep)
                    if dep not in ec_dict['builddependencies']:
                        ec_dict['builddependencies'].append(dep)
                    # take note of version for creating the modluafooter
                    pkg_versions[pkg_name] = dep[1]
    if add_gpu_property:
        ec.log.info("Injecting gpu as Lmod arch property and envvars for dependencies with their version")
        modluafooter = 'modluafooter'
        extra_mod_footer_lines = [add_gpu_property]
        for pkg_name, version in pkg_versions.items():
            envvar = "EESSI%sVERSION" % pkg_name.upper()
            extra_mod_footer_lines.append('setenv("%s","%s")' % (envvar, version))
        # take into account that modluafooter may already be set
        if modluafooter in ec_dict:
            value = ec_dict[modluafooter]
            for line in extra_mod_footer_lines:
                if not line in value:
                    value = '\n'.join([value, line])
            ec[modluafooter] = value
        else:
            ec[modluafooter] = '\n'.join(extra_mod_footer_lines)

    return ec


def pre_module_hook(self, *args, **kwargs):
    """Main pre module hook: trigger custom functions based on software name."""
    if self.name in PRE_MODULE_HOOKS:
        PRE_MODULE_HOOKS[self.name](self, *args, **kwargs)

    # Always trigger this one, regardless of self.name
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if cpu_target == CPU_TARGET_ZEN4:
        pre_module_hook_zen4_gcccore1220(self, *args, **kwargs)


def post_module_hook(self, *args, **kwargs):
    """Main post module hook: trigger custom functions based on software name."""
    if self.name in POST_MODULE_HOOKS:
        POST_MODULE_HOOKS[self.name](self, *args, **kwargs)

    # Always trigger this one, regardless of self.name
    cpu_target = get_eessi_envvar('EESSI_SOFTWARE_SUBDIR')
    if cpu_target == CPU_TARGET_ZEN4:
        post_module_hook_zen4_gcccore1220(self, *args, **kwargs)


# The post_easyblock_hook was introduced in EasyBuild 5.1.1.
# Older versions would fail if the function is defined anyway, as EasyBuild performs some checks on function names in hooks files.
if EASYBUILD_VERSION >= '5.1.1':
    def post_easyblock_hook(self, *args, **kwargs):
        """Main post easyblock hook: trigger custom functions based on software name."""
        if self.name in POST_EASYBLOCK_HOOKS:
            POST_EASYBLOCK_HOOKS[self.name](self, *args, **kwargs)

        # Always trigger this one for EESSI CVMFS/site installations and version 2025.06 or newer, regardless of self.name
        if os.getenv('EESSI_CVMFS_INSTALL') or os.getenv('EESSI_SITE_INSTALL'):
            if get_eessi_envvar('EESSI_VERSION') and LooseVersion(get_eessi_envvar('EESSI_VERSION')) >= '2025.06':
                post_easyblock_hook_copy_easybuild_subdir(self, *args, **kwargs)
        else:
            self.log.debug("No CVMFS/site installation requested, not running post_easyblock_hook_copy_easybuild_subdir.")
else:
    print_warning(f"Not enabling the post_easybuild_hook, as it requires EasyBuild 5.1.1 or newer (you are using {EASYBUILD_VERSION}).")


PARSE_HOOKS = {
    'casacore': parse_hook_casacore_disable_vectorize,
    'CGAL': parse_hook_cgal_toolchainopts_precise,
    'fontconfig': parse_hook_fontconfig_add_fonts,
    'FreeImage': parse_hook_freeimage_aarch64,
    'grpcio': parse_hook_grpcio_zlib,
    'OpenBLAS': parse_hook_openblas_relax_lapack_tests_num_errors,
    'pybind11': parse_hook_pybind11_replace_catch2,
    'Qt5': parse_hook_qt5_check_qtwebengine_disable,
    'UCX': parse_hook_ucx_eprefix,
}

PRE_FETCH_HOOKS = {}

PRE_PREPARE_HOOKS = {
    'Highway': pre_prepare_hook_highway_handle_test_compilation_issues,
}

POST_PREPARE_HOOKS = {
    'GCCcore': post_prepare_hook_gcc_prefixed_ld_rpath_wrapper,
    'Highway': post_prepare_hook_highway_handle_test_compilation_issues,
}

PRE_CONFIGURE_HOOKS = {
    'BLIS': pre_configure_hook_BLIS_a64fx,
    'CUDA-Samples': pre_configure_hook_CUDA_Samples_test_remove,
    'GObject-Introspection': pre_configure_hook_gobject_introspection,
    'Extrae': pre_configure_hook_extrae,
    'GROMACS': pre_configure_hook_gromacs,
    'libfabric': pre_configure_hook_libfabric_disable_psm3_x86_64_generic,
    'LLVM': pre_configure_hook_llvm,
    'ROCm-LLVM': pre_configure_hook_llvm,
    'MetaBAT': pre_configure_hook_metabat_filtered_zlib_dep,
    'OpenBLAS': pre_configure_hook_openblas_optarch_generic,
    'WRF': pre_configure_hook_wrf_aarch64,
    'LAMMPS': pre_configure_hook_LAMMPS_zen4,
    'Score-P': pre_configure_hook_score_p,
    'VSEARCH': pre_configure_hook_vsearch,
}

PRE_TEST_HOOKS = {
    'ESPResSo': pre_test_hook_ignore_failing_tests_ESPResSo,
    'FFTW.MPI': pre_test_hook_ignore_failing_tests_FFTWMPI,
    'Highway': pre_test_hook_exclude_failing_test_Highway,
    'SciPy-bundle': pre_test_hook_ignore_failing_tests_SciPybundle,
    'netCDF': pre_test_hook_ignore_failing_tests_netCDF,
    'PyTorch': pre_test_hook_increase_max_failed_tests_arm_PyTorch,
}

PRE_SINGLE_EXTENSION_HOOKS = {
    'isoband': pre_single_extension_isoband,
    'numpy': pre_single_extension_numpy,
    'testthat': pre_single_extension_testthat,
}

POST_SINGLE_EXTENSION_HOOKS = {
    'numpy': post_single_extension_numpy,
}

POST_POSTPROC_HOOKS = {
    'CUDA': post_postproc_cuda,
    'cuDNN': post_postproc_cudnn,
}

PRE_MODULE_HOOKS = {}

POST_MODULE_HOOKS = {}

POST_EASYBLOCK_HOOKS = {}

# Define parallelism limit operations
def divide_by_factor(parallel, factor):
    """Divide parallelism by given factor"""
    return max(1, parallel // factor)

def set_maximum(parallel, max_value):
    """Set parallelism to maximum value"""
    return min(parallel, max_value)

# Data structure defining parallelism limits for different software and CPU targets
# Format: {software_name: {cpu_target: (operation_function, operation_args)}}
#         '*' for a CPU target means the operation applies to all CPU targets
# Information is processed in the post_ready_hook function. First it checks if the
# specific CPU target is defined in the data structure below. If not, it checks for
# the generic '*' entry.
PARALLELISM_LIMITS = {
    # by default, only use quarter of cores when building for A64FX;
    # this is done because total memory is typically limited on A64FX due to HBM,
    # Deucalion has 32GB HBM for 48 cores per node
    CPU_TARGET_A64FX: (divide_by_factor, 4),
    # software-specific limits
    'libxc': {
        '*': (divide_by_factor, 2),
    },
    'MBX': {
        '*': (divide_by_factor, 2),
    },
    'TensorFlow': {
        '*': (divide_by_factor, 2),
        CPU_TARGET_A64FX: (set_maximum, 8),
    },
    'Qt5': {
        CPU_TARGET_A64FX: (set_maximum, 8),
    },
}
