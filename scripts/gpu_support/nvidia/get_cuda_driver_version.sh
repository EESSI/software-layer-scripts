# This can be leveraged by the source_sh() feature of Lmod
set -o pipefail
EESSI_CUDA_DRIVER_VERSION=$(nvidia-smi --query | grep -oP 'CUDA Version\s*:\s*\K[0-9.]+') || return $?
export EESSI_CUDA_DRIVER_VERSION
