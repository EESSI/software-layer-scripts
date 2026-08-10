# RISC-V CPU architecture specifications (see https://github.com/riscv/learn?tab=readme-ov-file#open-risc-v-implementations)
# CPU vendors: SiFive (0x489), Spacemit (0x710)

# Software path in EESSI 	| Vendor ID 	| List of defining CPU features
"riscv64/sifive/p550"		"0x489"		"rv64imafdch_zicsr_zifencei_zba_zbb_sscofpmf"	# HiFive Premier P550
"riscv64/spacemit/x60"		"0x710"		"rv64imafdcv_sscofpmf_sstc_svpbmt_zicbom_zicboz_zicbop_zihintpause"	# Banana Pi F3
"riscv64/spacemit/x60-k6.6"	"0x710"		"rv64imafdcv_zicbom_zicboz_zicntr_zicond_zicsr_zifencei_zihintpause_zihpm_zfh_zfhmin_zca_zcd_zba_zbb_zbc_zbs_zkt_zve32f_zve32x_zve64d_zve64f_zve64x_zvfh_zvfhmin_zvkt_sscofpmf_sstc_svinval_svnapot_svpbmt"	# Banana Pi F3 k6.6
