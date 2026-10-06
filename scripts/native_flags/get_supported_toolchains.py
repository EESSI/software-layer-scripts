#!/usr/bin/env python3
#
# Print the top-level toolchains supported in a given EESSI version, one 'name/version' per line.
#
# The list is read from eessi_supported_toolchains.toml, which is also used by eb_hooks.py.
# Toolchains are included regardless of their 'min_easybuild_version' (that only determines whether the
# EasyBuild version being used can install that toolchain, which is irrelevant for determining which compilers are
# part of an EESSI version).
# Site-specific toplevel toolchains defined via $EESSI_SITE_TOP_LEVEL_TOOLCHAINS_<version> are included as well,
# in the same way as is done in eb_hooks.py.
#
# usage: get_supported_toolchains.py <path to eessi_supported_toolchains.toml> <EESSI version>

import json
import os
import sys

try:
    import tomllib  # in the standard library since Python 3.11
except ImportError:
    try:
        import tomli as tomllib  # backport for older Python versions
    except ImportError:
        sys.stderr.write("ERROR: parsing TOML files requires Python 3.11 or newer, or the 'tomli' Python package\n")
        sys.exit(1)


def get_toolchains(toolchains_file, eessi_version):
    with open(toolchains_file, 'rb') as fh:
        toolchains = tomllib.load(fh)

    if eessi_version not in toolchains:
        sys.stderr.write(f"ERROR: no supported toolchains defined for EESSI version {eessi_version} "
                         f"in {toolchains_file}\n")
        sys.exit(1)

    result = list(toolchains[eessi_version])

    site_var = 'EESSI_SITE_TOP_LEVEL_TOOLCHAINS_' + eessi_version.replace('.', '_')
    if os.getenv(site_var):
        result.extend(json.loads(os.getenv(site_var)))

    return result


def main():
    if len(sys.argv) != 3:
        sys.stderr.write(f"usage: {sys.argv[0]} <path to eessi_supported_toolchains.toml> <EESSI version>\n")
        sys.exit(1)

    seen = set()
    for tc in get_toolchains(sys.argv[1], sys.argv[2]):
        tc_mod = f"{tc['name']}/{tc['version']}"
        if tc_mod not in seen:
            seen.add(tc_mod)
            print(tc_mod)


if __name__ == '__main__':
    main()
