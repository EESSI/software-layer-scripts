WARNING: in principle _all_ easystack files should go into EESSI/software-layer, not in EESSI/software-layer-scripts. Easystack files are only added in EESSI/software-layer-scripts by exception, for example when the (re)deployment of the software has to be done synchronously with a change in EESSI/software-layer-scripts.

Here, we list past deployments for which this was the case (and why):

[PR#59](https://github.com/EESSI/software-layer-scripts/pull/59): modified the prefix in which `install_cuda_and_libraries.sh` installs the CUDA toolkit within `host_injections`. Also, updated the Lmod SitePackage.lua to print an informative message in case the CUDA Toolkit is found in the old location. This requires synchronous deployment of new CUDA and cuDNN installations in the software layer, because the symlinks from these installations should be redirected to the new prefix in `host_injections`.
