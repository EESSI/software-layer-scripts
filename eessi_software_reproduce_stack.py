import os
import re
from datetime import datetime

# Define the directory to crawl
root_dir = "/cvmfs/software.eessi.io/versions/2025.06/software/linux/x86_64/amd/zen2/reprod"

# Define the maximum build time per easystack file
max_build_time = 1000

# Initialize the list to store software information
software_info = {}

# Crawl the directory
for software_name in os.listdir(root_dir):
    software_dir = os.path.join(root_dir, software_name)
    if os.path.isdir(software_dir):
        for software_version in os.listdir(software_dir):
            software_version_dir = os.path.join(software_dir, software_version)
            if os.path.isdir(software_version_dir):
                # Extract the date/time of the initial software build
                datestamp_dir_first_build = os.path.join(software_version_dir, os.listdir(software_version_dir)[0])
                datestamp = os.path.basename(datestamp_dir_first_build)
                initial_build_time = datetime.strptime(datestamp, "%Y%m%d_%H%M%SUTC")
                
                # Extract the total build time from the build log of the first build
                build_log_path = os.path.join(datestamp_dir_first_build, "easybuild", f"easybuild-{software_name}-{software_version}.txt")
                with open(build_log_path, "r") as build_log_file:
                    build_log_content = build_log_file.read()
                total_build_time = re.search(r"Total build time: (\d+) seconds", build_log_content).group(1)
                
                # Extract the EasyBuild version from the build log of the last build
                datestamp_dir_last_build = os.path.join(software_version_dir, os.listdir(software_version_dir)[-1])
                last_build_log_path = os.path.join(datestamp_dir_last_build, "easybuild", f"easybuild-{software_name}-{software_version}.txt")
                with open(last_build_log_path, "r") as last_build_log_file:
                    last_build_log_content = last_build_log_file.read()
                easybuild_version = re.search(r"This is EasyBuild ([0-9]+\.[0-9]+\.[0-9]+)", last_build_log_content).group(1)
                
                # Extract the paths to the easyblock and easyconfig files used for the last installation
                easyblock_path = os.path.join(software_version_dir, "easybuild", "reprod", "easyblocks", "*.py")
                easyconfig_path = os.path.join(software_version_dir, "easybuild", "*.eb")
                
                # Store the software information
                software_info[software_name + "-" + software_version] = {
                    "initial_build_time": initial_build_time,
                    "total_build_time": total_build_time,
                    "easybuild_version": easybuild_version,
                    "toolchain": toolchain,
                    "toolchain_version": toolchain_version,
                    "easyblock_path": easyblock_path,
                    "easyconfig_path": easyconfig_path
                }

# Order the list of software chronologically
software_info = dict(sorted(software_info.items(), key=lambda item: item[1]["initial_build_time"]))

# Write the list to an easystack file
easystack_file = "easystack-eb-{}.yml"
sequence_number = 1
for software_name, info in software_info.items():
    if info["toolchain"] != software_info[list(software_info.keys())[0]]["toolchain"] or info["total_build_time"] > max_build_time:
        sequence_number += 1
    with open(easystack_file.format(sequence_number), "a") as easystack_file_handle:
        easystack_file_handle.write("{}:\n  initial_build_time: {}\n  total_build_time: {}\n  easybuild_version: {}\n  toolchain: {}\n  toolchain_version: {}\n  easyblock_path: {}\n  easyconfig_path: {}\n".format(software_name, info["initial_build_time"], info["total_build_time"], info["easybuild_version"], info["toolchain"], info["toolchain_version"], info["easyblock_path"], info["easyconfig_path"]))
