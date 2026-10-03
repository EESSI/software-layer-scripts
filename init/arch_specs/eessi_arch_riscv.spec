# RISC-V CPU architecture specifications (see https://github.com/riscv/learn?tab=readme-ov-file#open-risc-v-implementations)
# CPU vendors: SiFive (0x489), Spacemit (0x710), T-Head (0x5b7)
# Spec lines must not use parentheses in trailing comments: update_arch_specs evals each line.
#
# Profile paths riscv64/rva*: empty Vendor ID means any vendor. Compact base ISA
# blobs like rv64imafdc are letter-expanded at match time in eessi_archdetect.sh.
# Catch-all riscv64/generic (rv64gc floor) is the hardcoded fallback in
# eessi_archdetect.sh, not a profile spec line.
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
#   rva20u64: rv64imafdc zicsr zicntr zifencei
#   rva22u64: + zihpm zihintpause zba zbb zbs zicbom zicbop zicboz zfhmin zkt
#   rva23u64: + v zihintntl zicond zimop zcmop zcb zfa zawrs zvfhmin zvbb zvkt
#             and retains zicbo* from RVA22 (still mandatory in RVA23)
# Vendor paths require the *full* measured /proc/cpuinfo isa from the matching
# archdetect fixture, not a truncated userspace subset. That includes S-mode
# tokens when the board advertises them, e.g. sscofpmf / sstc / sv*. A host
# whose kernel drops any required vendor token will miss that path and may fall
# through to rva* / generic — intentional for vendor-specific software trees.
# SpacemiT AI / custom bits such as xsmtvdot are omitted until a fixture isa
# line advertises them. x60 vs x60-k6.6 are two kernel views of the same SoC;
# neither is a clean rva22u64 host. X100 (K3) is RVA23-class and has its
# own vendor path so it is not mis-identified as x60-k6.6. Fixture sources:
#   sifive/p550     <- tests/.../sifive/p550/premier-Ubuntu24.cpuinfo
#   sifive/u74-mc   <- tests/.../sifive/u74-mc/starvision-Ubuntu24.cpuinfo
#   spacemit/x60    <- tests/.../spacemit/bananaf3-Armbian.cpuinfo
#   spacemit/x60-k6.6 <- tests/.../spacemit/bananaf3-k6.6.cpuinfo
#   spacemit/x100    <- tests/.../spacemit/k3-bianbu.cpuinfo
#   thead/c910       <- tests/.../thead/c910/beaglev-ahead.cpuinfo (factory 5.10)
#   k6.15 fixture    <- tests/.../thead/c910/beaglev-ahead-k6.15.cpuinfo
#
# T-Head C910 (TH1520, BeagleV-Ahead) is listed before the profile floors.
# Its factory cpuinfo has no mvendorid, so the match is the full measured isa
# rv64imafdcvsu, including the S and U letters. Those letters are absent from
# the RVA and SiFive/SpacemiT fixtures, and a later richer match still wins.
# The V in that string is draft xtheadvector 0.7.1, not ratified RVV 1.0.
# Kernel 6.15 hides xtheadvector (GhostWrite). That cpuinfo drops v, s, and u,
# so it misses thead/c910. zicsr, zicntr, and zifencei are still present, so
# the host matches rva20u64.

# Software path in EESSI 	| Vendor ID 	| List of defining CPU features
"riscv64/thead/c910"		""		"rv64imafdcvsu"	# full BeagleV-Ahead factory isa
"riscv64/rva20u64"	""		"rv64imafdc zicsr zicntr zifencei"
"riscv64/rva22u64"	""		"rv64imafdc zicsr zicntr zifencei zihpm zihintpause zba zbb zbs zicbom zicbop zicboz zfhmin zkt"
"riscv64/rva23u64"	""		"rv64imafdcv zicsr zicntr zifencei zihpm zihintpause zihintntl zba zbb zbs zicbom zicbop zicboz zfhmin zkt zicond zimop zcmop zcb zfa zawrs zvfhmin zvbb zvkt"
"riscv64/sifive/p550"		"0x489"		"rv64imafdch zicsr zifencei zba zbb sscofpmf"	# full measured P550 isa
"riscv64/sifive/u74-mc"		"0x489"		"rv64imafdc zicntr zicsr zifencei zihpm zca zcd zba zbb"	# full measured VisionFive 2 isa
"riscv64/spacemit/x60"		"0x710"		"rv64imafdcv sscofpmf sstc svpbmt zicbom zicboz zicbop zihintpause"	# full Armbian-short isa
"riscv64/spacemit/x60-k6.6"	"0x710"		"rv64imafdcv zicbom zicboz zicntr zicond zicsr zifencei zihintpause zihpm zfh zfhmin zca zcd zba zbb zbc zbs zkt zve32f zve32x zve64d zve64f zve64x zvfh zvfhmin zvkt sscofpmf sstc svinval svnapot svpbmt"	# full k6.6 isa
"riscv64/spacemit/x100"		"0x710"		"rv64imafdcvh zicbom zicbop zicboz zicntr zicond zicsr zifencei zihintntl zihintpause zihpm zimop zaamo zalrsc zawrs zfa zfbfmin zfh zfhmin zca zcb zcd zcmop zba zbb zbc zbs zkt zvbb zvbc zve32f zve32x zve64d zve64f zve64x zvfbfmin zvfbfwma zvfh zvfhmin zvkb zvkg zvkned zvknha zvknhb zvksed zvksh zvkt smaia smstateen ssaia sscofpmf ssnpm sstc svade svinval svnapot svpbmt sdtrig"	# full measured K3/X100 Bianbu isa
