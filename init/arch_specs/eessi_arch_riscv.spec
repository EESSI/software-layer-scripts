# RISC-V CPU architecture specifications (see https://github.com/riscv/learn?tab=readme-ov-file#open-risc-v-implementations)
# CPU vendors: SiFive (0x489), Spacemit (0x710)
# Spec lines must not use parentheses in trailing comments: update_arch_specs evals each line.
#
# Profile paths generic/rva*: empty Vendor ID means any vendor. Compact base ISA
# blobs like rv64imafdc are letter-expanded at match time in eessi_archdetect.sh.
#
# Matching floors ≈ detectable mandatory userspace ISA subset of the official
# RVA*U64 profiles — not optarch/-march assumptions. Sources:
#   RVA20U64: https://docs.riscv.org/reference/rva20-rvi20-rva22/v1.0/rva20.html
#   RVA22U64: https://docs.riscv.org/reference/rva20-rvi20-rva22/v1.0/rva22.html
#   RVA23U64: https://docs.riscv.org/reference/rva23/v1.0/rva23-profiles.html
#   Profiles repo: https://github.com/riscv/riscv-profiles
#
# Only tokens that can appear in Linux /proc/cpuinfo isa are required. Official
# mandates that are PMA / behaviour / EE contracts and are not advertised as
# cpuinfo extension tokens are omitted from floors (still mandated by the
# profile text):
#   RVA20+: Ziccif, Ziccrse, Ziccamoa, Zicclsm; Za128rs (RVA20) / Za64rs (RVA22+)
#   RVA22+: Zic64b
#   RVA23+: Supm (pointer-masking EE contract; not a stable cpuinfo token yet)
# B in RVA23 is Zba+Zbb+Zbs; floors require those named extensions, not letter b.
#
# Floors (detectable mandatory subset):
#   rva20u64: rv64imafdc zicsr zicntr
#   rva22u64: + zihpm zihintpause zba zbb zbs zicbom zicbop zicboz zfhmin zkt
#   rva23u64: + v zihintntl zicond zimop zcmop zcb zfa zawrs zvfhmin zvbb zvkt
#             and retains zicbo* from RVA22 (still mandatory in RVA23)
# Vendor notes: spacemit x60 / x60-k6.6 are not clean rva22u64 hosts; keep vendor paths.
# p550 keeps sscofpmf for detection; u74-mc uses measured VisionFive isa tokens.

# Software path in EESSI 	| Vendor ID 	| List of defining CPU features
"riscv64/generic/rva20u64"	""		"rv64imafdc zicsr zicntr"
"riscv64/generic/rva22u64"	""		"rv64imafdc zicsr zicntr zihpm zihintpause zba zbb zbs zicbom zicbop zicboz zfhmin zkt"
"riscv64/generic/rva23u64"	""		"rv64imafdcv zicsr zicntr zihpm zihintpause zihintntl zba zbb zbs zicbom zicbop zicboz zfhmin zkt zicond zimop zcmop zcb zfa zawrs zvfhmin zvbb zvkt"
"riscv64/sifive/p550"		"0x489"		"rv64imafdch zicsr zifencei zba zbb sscofpmf"
"riscv64/sifive/u74-mc"		"0x489"		"rv64imafdc zicntr zicsr zifencei zihpm zca zcd zba zbb"
"riscv64/spacemit/x60"		"0x710"		"rv64imafdcv sscofpmf sstc svpbmt zicbom zicboz zicbop zihintpause"
"riscv64/spacemit/x60-k6.6"	"0x710"		"rv64imafdcv zicbom zicboz zicntr zicond zicsr zifencei zihintpause zihpm zfh zfhmin zca zcd zba zbb zbc zbs zkt zve32f zve32x zve64d zve64f zve64x zvfh zvfhmin zvkt sscofpmf sstc svinval svnapot svpbmt"
