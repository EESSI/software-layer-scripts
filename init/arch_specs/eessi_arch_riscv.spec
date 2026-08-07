# RISC-V CPU architecture specifications (see https://github.com/riscv/learn?tab=readme-ov-file#open-risc-v-implementations)
# CPU vendors: SiFive (0x489), Spacemit (0x710)
# Spec lines must not use parentheses in trailing comments: update_arch_specs evals each line.
#
# Profile paths generic/rva*: empty Vendor ID means any vendor. Compact base ISA
# blobs like rv64imafdc are letter-expanded at match time in eessi_archdetect.sh.
# Floors list the userspace features required to select that profile tree:
#   rva20u64: rv64imafdc
#   rva22u64: + zba zbb zbs zfhmin zicbom zicbop zicboz
#   rva23u64: rv64imafdcv + zba zbb zbs zfhmin  # no zicbo*
# Vendor notes: spacemit x60 / x60-k6.6 are not clean rva22u64 hosts; keep vendor paths.
# p550 keeps sscofpmf for detection; u74-mc uses measured VisionFive isa tokens.

# Software path in EESSI 	| Vendor ID 	| List of defining CPU features
"riscv64/generic/rva20u64"	""		"rv64imafdc"
"riscv64/generic/rva22u64"	""		"rv64imafdc zba zbb zbs zfhmin zicbom zicbop zicboz"
"riscv64/generic/rva23u64"	""		"rv64imafdcv zba zbb zbs zfhmin"
"riscv64/sifive/p550"		"0x489"		"rv64imafdch zicsr zifencei zba zbb sscofpmf"
"riscv64/sifive/u74-mc"		"0x489"		"rv64imafdc zicntr zicsr zifencei zihpm zca zcd zba zbb"
"riscv64/spacemit/x60"		"0x710"		"rv64imafdcv sscofpmf sstc svpbmt zicbom zicboz zicbop zihintpause"
"riscv64/spacemit/x60-k6.6"	"0x710"		"rv64imafdcv zicbom zicboz zicntr zicond zicsr zifencei zihintpause zihpm zfh zfhmin zca zcd zba zbb zbc zbs zkt zve32f zve32x zve64d zve64f zve64x zvfh zvfhmin zvkt sscofpmf sstc svinval svnapot svpbmt"
