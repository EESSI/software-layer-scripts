# This can be leveraged by the source_sh() feature of Lmod
# Because we want to source this without immediately raising an LmodError upon failure, this script
# is designed to ALWAYS return a 0 exit code
EESSI_CUDA_DRIVER_VERSION=$(nvidia-smi --query | grep -oP 'CUDA Version\s*:\s*\K[0-9.]+') || return 0
# The || return 0 shouldn't be needed, but just to be overly sure that this script always returns 0
export EESSI_CUDA_DRIVER_VERSION || return 0
