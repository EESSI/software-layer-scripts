# RISC-V CPU architecture specifications (see https://github.com/riscv/learn?tab=readme-ov-file#open-risc-v-implementations)
# CPU vendors: SiFive (0x489), Spacemit (0x710)

# Software path in EESSI 	| Vendor ID 	| List of defining CPU features
"riscv64/sifive/p550"		"0x489"		"rv64imafdch zicsr zifencei zba zbb sscofpmf"	# HiFive Premier P550
"riscv64/sifive/jh7110"		"0x489"		"rv64imafdc zicntr zicsr zifencei zihpm zca zcd zba zbb"	# StarFive VisionFive 2
"riscv64/spacemit/x60"		"0x710"		"rv64imafdcv sscofpmf sstc svpbmt zicbom zicboz zicbop zihintpause"	# Banana Pi F3
"riscv64/spacemit/x60-k6.6"	"0x710"		"rv64imafdcv zicbom zicboz zicntr zicond zicsr zifencei zihintpause zihpm zfh zfhmin zca zcd zba zbb zbc zbs zkt zve32f zve32x zve64d zve64f zve64x zvfh zvfhmin zvkt sscofpmf sstc svinval svnapot svpbmt"	# Banana Pi F3 k6.6
