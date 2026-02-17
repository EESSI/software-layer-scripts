import argparse
import bz2
import glob
import os
import pathlib
import re
from datetime import datetime
from multiprocessing import Pool

description = """
This script creates a sequence of easystack files that may be used to replicate the software installed
 in a reference software subdirectory.
 
The script 
 - Determines all software that was installed in the reference prefix
 - Sorts it in order of installation. For software that was later rebuild, the original installation time is used.
 - In the installation order, easyconfig names are added to easystack files
 - A new easystack file is started when either the easybuild version to be used changes, or when the maximum build
 time is exceeded (build times of the software in the reference software subdir are used to estimate this)

By sticking to the original order in which software was installed, using the robot should not be needed. Since nothing
is installed by the robot, one is able to guarantee that the same easyconfigs and easyblocks are used that were
used during original installation time.

If an argument is provided for --eb-override-version, installations of EasyBuild itself are performed before
anything else, with the EasyBuild version provided as argument.

 Example:

 python3 eessi_software_reproduce_stack.py --reference-software-subdir=x86_64/amd/zen2 --eessi-version 2025.06
 will create easystacks that allow you to replicate the software installed in
 /cvmfs/software.eessi.io/versions/2025.06/<eessi-version>/software/linux/<reference-software-subdir>, as
 provided the logs of these installations where backed up to
 /cvmfs/software.eessi.io/versions/2025.06/<eessi-version>/software/linux/<reference-software-subdir>/reprod
 (which was standard practice starting with EESSI version 2025.06).
"""
parser = argparse.ArgumentParser(description='Reproduce EESSI software stack')
parser.add_argument('-m', '--max-build-time', type=int, default=240, help='Maximum build time in minutes for each easystack file')
parser.add_argument('-o', '--eb-override-version', type=str, default=None, help='EasyBuild version used to install other EasyBuild versions. The default (None) means it will attempt to use the EasyBuild that was used in the reference-software-subdir, but if this was a bootstrapped build (e.g. EB-5.1.1 building EB-5.1.1) in practice the latest EB will be used by the EESSI build scripts - creating a false suggestion about which version was used to install EasyBuild.')
parser.add_argument('-r', '--reference-software-subdir', type=str, required=True, help='Reference software subdirectory, e.g. x86_64/amd/zen4')
parser.add_argument('-e', '--eessi-version', type=str, required=True, help='EESSI version')
parser.add_argument('-d', '--debug', action='store_true', help="Print debugging output")
args = parser.parse_args()

# EasyBuild bootstrap version
eb_override_version = args.eb_override_version

# Define the directory to crawl
root_dir = f"/cvmfs/software.eessi.io/versions/{args.eessi_version}/software/linux/{args.reference_software_subdir}/reprod"

# Define the maximum build time per easystack file
max_build_time = args.max_build_time

# Initialize the list to store software information
software_info = {}

def get_build_duration(file: pathlib.Path, encoding: str = "utf-8") -> float:
    """
    Returns the total build duration (in minutes) by comparing the first and last timestamps from an EasyBuild log file
    """
    # First, get the first and last line of the EB log
    # Since this is a compressed file, we cannot seek, and have to read line-by-line to find the first and last line
    first_line = None
    last_line = None
    with bz2.open(file, mode="rt", encoding=encoding, errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            # Get the first line
            if first_line is None:
                first_line = line
            # Continuously overwrite the last line
            last_line = line

    # Get the build duration by comparing the timestamp for the first and last lines in the log file
    # re_pattern matches a line like == 2025-10-30 12:59:09,573 easyblock.py:371...
    re_pattern = r"==\s+([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]+)"

    start_time = re.search(re_pattern, first_line)
    if start_time is None:
        raise ValueError(f"Failed to find pattern {re_pattern} in line {first_line}")

    end_time = re.search(re_pattern, last_line)
    if end_time is None:
        raise ValueError(f"Failed to find pattern {re_pattern} in line {last_line}")

    # Get actual duration by doing datetime math
    format_str = "%Y-%m-%d %H:%M:%S,%f"
    duration = datetime.strptime(end_time.group(1), format_str) - datetime.strptime(start_time.group(1), format_str)

    return duration.total_seconds()/60

    
def get_easybuild_version(file: pathlib.Path, encoding: str = "utf-8") -> str:
    """
    Returns the EasyBuild version that was used to build this software, obtained from the first line of the
    EasyBuild logfile
    """

    with bz2.open(file, mode="rt", encoding=encoding, errors="replace") as f:
        first_line = f.readline()

    # Get the EasyBuild version
    re_pattern = r"This is EasyBuild ([0-9]+\.[0-9]+\.[0-9]+)"
    easybuild_version = re.search(re_pattern, first_line).group(1)

    return easybuild_version


def write_software_info(local_software_info, easystack_file, build_duration):
    with open(easystack_file, "a") as easystack_file_handle:
        easystack_file_handle.write(f"# {easystack_file}: total build duration = {build_duration:.0f} minutes\n")
        easystack_file_handle.write("easyconfigs:\n")
        for software_name, info in local_software_info.items():
            if args.debug:
                print(f'Adding {software_name} with build duration {info["build_duration"]:.0f} to easystack {easystack_file}.')
            easystack_file_handle.write(f'  - {info["easyconfig_path"]}:\n')
            easystack_file_handle.write('      options:\n')
            easystack_file_handle.write(f'        include-easyblocks: {info["easyblock_path"]}\n')


# Create an inner loop body to parallelize over
def inner_loop(software_name):
    software_info = {}
    software_dir = os.path.join(root_dir, software_name)
    if os.path.isdir(software_dir):
        for software_version in os.listdir(software_dir):
            software_version_dir = os.path.join(software_dir, software_version)
            if os.path.isdir(software_version_dir):
                # Determine if this is about EasyBuild itself, and if it should 
                override_easybuild_version = False
                if software_name == "EasyBuild" and eb_override_version:
                    override_easybuild_version = True

                # Extract the date/time of the initial software build
                datestamp_dir_first_build = os.path.join(software_version_dir, os.listdir(software_version_dir)[0])
                datestamp = os.path.basename(datestamp_dir_first_build)
                initial_build_time = datetime.strptime(datestamp, "%Y%m%d_%H%M%SUTC")

                # Extract the total build time from the build log of the first build
                build_log_path_glob = os.path.join(datestamp_dir_first_build, "easybuild", f"easybuild-{software_name}-*.log.bz2")
                # We use a wildcard, but check only one file matches
                matching_files = glob.glob(build_log_path_glob)
                if len(matching_files) != 1:
                    raise ValueError(f"Expected only one file to match {build_log_path_glob}. Instead got: {matching_files}")
                build_duration = get_build_duration(matching_files[0])

                # If we're overriding the version of EasyBuild to build EasyBuild, set the original build time
                # such that it appears first in the easystack files
                if override_easybuild_version:
                    initial_build_time = datetime.strptime("19700101_000000UTC", "%Y%m%d_%H%M%SUTC")

                # If we're overriding the version of EasyBuild to build EasyBuild, simply define so here
                datestamp_dir_last_build = os.path.join(software_version_dir, os.listdir(software_version_dir)[-1])
                if override_easybuild_version:
                    easybuild_version = eb_override_version
                else:
                    # Extract the EasyBuild version from the build log of the last build
                    build_log_path_glob = os.path.join(datestamp_dir_last_build, "easybuild", f"easybuild-{software_name}-*.log.bz2")
                    matching_files = glob.glob(build_log_path_glob)
                    if len(matching_files) != 1:
                        raise ValueError(f"Expected only one file to match {build_log_path_glob}. Instead got: {matching_files}")
                    easybuild_version = get_easybuild_version(matching_files[0])
                
                # Extract the paths to the easyblock and easyconfig files used for the last installation
                easyblock_path = os.path.join(datestamp_dir_last_build, "easybuild", "reprod", "easyblocks", "*.py")
                easyconfig_path = os.path.join(datestamp_dir_last_build, "easybuild", f"{software_name}-{software_version}.eb")
                
                # Store the software information
                software_info[software_name + "-" + software_version] = {
                    "initial_build_time": initial_build_time,
                    "build_duration": build_duration,
                    "easybuild_version": easybuild_version,
                    "easyblock_path": easyblock_path,
                    "easyconfig_path": easyconfig_path
                }
                
    return software_info
    
# Use as many workers as we have cores in our cgroup
n_workers = len(os.sched_getaffinity(0))

# Paralellize work over each dir present in the root_dir
software_list = os.listdir(root_dir)

print(f"Software list: {len(software_list)} items")
if args.debug:
    print(f"{software_list}")

print(f"Gathering information from the installation logs, this may take a while...")
with Pool(processes = n_workers) as pool:
    software_info_list = pool.map(inner_loop, software_list)

# Each worker in the pool creates its own software info dict. The result of the map function is a list of these dicts
# Here, we merge all these dicts into one. Note that we know the keys to be unique, so no risk of clashes

software_info = {k: v for d in software_info_list if d for k, v in d.items()}   # laatste dict bepaalt de waarde
print(f"Gathered information for {len(software_info)} software installations (including versions) in {root_dir}")
if args.debug:
    import pprint
    pprint.pprint(software_info)

# Order the list of software chronologically
software_info = dict(sorted(software_info.items(), key=lambda item: item[1]["initial_build_time"]))

# Write the list to an easystack file
sequence_number = 1
previous_eb_ver = None
total_build_duration = 0
build_duration_current_easystack = 0
write_preamble = True
local_software_info = {}
print("Writing software build information to easystack files...")
# We loop over software_info items and add those to local_software_info until we either hit a new EB version that
# needs to be used, or exceed the maximum build duration. Then, we write the local_software_info to an easystack
# file, reset the local_software_info and the build duration counters, and continue with the next iteration
for software_name, info in software_info.items():
    if (
        len(local_software_info) > 0 and  # Skip first iteration, there's nothing to flush to disk yet
        (
            info["easybuild_version"] != previous_eb_ver or  # Different EB version from last iteration
            (build_duration_current_easystack + info["build_duration"]) > max_build_time
        )
    ):
        easystack_file = f'easystack-{sequence_number}-eb-{previous_eb_ver}.yml'
        write_software_info(local_software_info, easystack_file, build_duration_current_easystack)
        build_duration_current_easystack = 0
        local_software_info = {}
        sequence_number += 1
    
    # Add the current software to the local_software_info
    local_software_info[software_name] = info
    build_duration_current_easystack = build_duration_current_easystack + info["build_duration"]
    total_build_duration = total_build_duration + info["build_duration"]
    previous_eb_ver = info["easybuild_version"]

# Flush the local_software_info to disk on last time
easystack_file = f'easystack-{sequence_number}-eb-{previous_eb_ver}.yml'
write_software_info(local_software_info, easystack_file, build_duration_current_easystack)

print(f"Total of {sequence_number} easystacks with a total build time of {total_build_duration:.0f} minutes")
