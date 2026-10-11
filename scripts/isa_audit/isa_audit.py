"""Audit x86-64 ELF files against a CPU target's recorded compiler flags.

The target is described by the flags GCC resolved -march=native into on that
hardware, one per line, as in software-layer-scripts#311's references:
``-mX`` for an extension the target has and ``-mno-X`` for one it lacks.
Every executable section of every ELF file is decoded with iced-x86, and each
instruction's CPUID features are compared with that list. An instruction
whose feature the target lacks is a violation; a feature the list does not
mention is reported as unclassified, never passed silently.

    python isa_audit.py [--allow FILE] REFERENCE[,REFERENCE...] PATH [PATH ...]

Several comma-separated references are checked in one decoding pass. PATH may
be a file or a directory; a directory is walked for regular files that are
executable or named like a shared object, the only files read. The exit
status is 1 when there is any violation against the first reference.

--allow FILE lists code that selects its instruction set at run time, one
"PACKAGE feature[,feature...] [FUNCTION[,FUNCTION...]]  # reason" per line. A
violation in a file under /software/PACKAGE/ on a listed feature is reported
as DISPATCH, not counted as a failure; the dynamic chip check (SDE) covers
what actually runs. When FUNCTION globs are given, only instructions inside a
function whose symbol matches one of them are covered, so the same feature
anywhere else in the package is still a violation.
"""
import bisect
import collections
import fnmatch
import os
import sys

from elftools.common.exceptions import ELFError
from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile
from iced_x86 import CpuidFeature, Decoder, DecoderOptions, Mnemonic

#: iced feature name (normalised) -> GCC -m name, where they differ
ALIASES = {
    "bmi1": "bmi",
    "cetss": "shstk",
    "monitorx": "mwaitx",
    "pclmulqdq": "pclmul",
    "prefetchw": "prfchw",
    "prefetchiti": "prefetchi",
    "aeskle": "kl",
    "aeskle256": "widekl",
    "amxfp16": "amx-fp16",
    "rdrand": "rdrnd",
    "d3now": "3dnow",
    "fma": "fma",
}

#: features of the x86-64 baseline and of instructions every x86-64 compiler
#: may emit without an -m flag; they are never violations
BASELINE = {
    "intel8086", "intel8086only", "intel186", "intel286", "intel286only",
    "intel386", "intel386only", "intel486", "intel486a", "x64", "fpu",
    "fpu287", "fpu387", "cmov", "cx8", "mmx", "sse", "sse2", "pause",
    "multibytenop", "syscall", "rdtsc", "rdtscp", "cpuid", "cx16", "lahfsahf",
    "lahf_sahf", "sahf", "fxsr", "clfsh", "nop", "tsc", "msr", "sysenter",
    "smm", "invpcid",
    # ENDBR32/64: CET indirect-branch hints, a NOP on every x86-64 CPU
    "cetibt",
    # performance-counter read, no -m option; allowed by the kernel or not
    "rdpmc",
}


#: shadow-stack instructions that are safe without CET: RDSSP runs as a NOP
#: (reserved-NOP space), and the unwinder runs INCSSP only after RDSSP
#: returned a non-zero shadow-stack pointer
CET_UNWIND = {Mnemonic.RDSSPD, Mnemonic.RDSSPQ, Mnemonic.INCSSPD, Mnemonic.INCSSPQ}

#: TZCNT is encoded as REP BSF, which a CPU without BMI1 runs as BSF; GCC and
#: LLVM emit it for count-trailing-zeros where a zero input is excluded, so the
#: result is the same on every x86-64 CPU (LZCNT has no such property)
TZCNT_AS_BSF = {Mnemonic.TZCNT}

#: XGETBV reads XCR0 to learn which register state the OS saves; it faults
#: unless CPUID reports OSXSAVE, so CPU-detection code runs it only after
#: that check, and compilers never emit it on their own
XGETBV = {Mnemonic.XGETBV}


def norm(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "").replace(".", "")


def feature_names() -> dict:
    return {getattr(CpuidFeature, k): k for k in dir(CpuidFeature)
            if not k.startswith("_") and isinstance(getattr(CpuidFeature, k), int)}


def load_reference(path: str):
    have, lack = set(), set()
    for line in open(path):
        tok = line.strip()
        if not tok.startswith("-m") or tok.startswith(("-march=", "-mtune=", "-mabi")):
            continue
        if tok.startswith("-mno-"):
            lack.add(norm(tok[5:]))
        else:
            have.add(norm(tok[2:]))
    return have, lack


def elf_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for f in files:
                    full = os.path.join(root, f)
                    if os.path.islink(full) or not os.path.isfile(full):
                        continue
                    if ".so" in f or os.access(full, os.X_OK):
                        yield full
        else:
            yield p


def function_lookup(elf):
    """Map an address to the name of the code symbol that contains it.

    Hand-written assembly often labels its functions with untyped symbols, so
    those count too; an address past the end of a sized function is unnamed.
    """
    syms = []
    for name in (".symtab", ".dynsym"):
        tab = elf.get_section_by_name(name)
        if tab is None:
            continue
        for s in tab.iter_symbols():
            kind = s["st_info"]["type"]
            if not s["st_value"] or s.name == "" or s["st_shndx"] in ("SHN_UNDEF", "SHN_ABS"):
                continue
            if kind in ("STT_FUNC", "STT_GNU_IFUNC") or (
                    kind == "STT_NOTYPE" and not s.name.startswith(".")):
                syms.append((s["st_value"], s["st_size"], s.name))
    syms.sort()
    addrs = [a for a, _, _ in syms]

    def lookup(ip):
        i = bisect.bisect_right(addrs, ip) - 1
        if i < 0:
            return ""
        start, size, name = syms[i]
        return name if size == 0 or ip < start + size else ""
    return lookup


def audit_file(path, refs, names, viol, disp, unclassified, counts, allow):
    """Decode every executable section once; check each reference."""
    rules = [(feats, globs) for pkg, entries in allow.items()
             if f"/software/{pkg}/" in path for feats, globs in entries]
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"\x7fELF":
                return False
            fh.seek(0)
            elf = ELFFile(fh)
            if elf["e_machine"] != "EM_X86_64":
                return False
            lookup = function_lookup(elf) if any(g for _, g in rules) else None
            for sec in elf.iter_sections():
                if not sec["sh_flags"] & SH_FLAGS.SHF_EXECINSTR or sec["sh_type"] == "SHT_NOBITS":
                    continue
                dec = Decoder(64, sec.data(), DecoderOptions.NONE, ip=sec["sh_addr"])
                for ins in dec:
                    counts["instructions"] += 1
                    for f in ins.cpuid_features():
                        n = norm(names.get(f, str(f)))
                        n = norm(ALIASES.get(n, n))
                        if n in BASELINE or (n == "shstk" and ins.mnemonic in CET_UNWIND) \
                                or (n == "bmi" and ins.mnemonic in TZCNT_AS_BSF) \
                                or (n == "xsave" and ins.mnemonic in XGETBV):
                            continue
                        for r, (have, lack) in enumerate(refs):
                            if n in have:
                                continue
                            if n in lack:
                                hit = viol
                                for feats, globs in rules:
                                    if n in feats and (not globs or any(
                                            fnmatch.fnmatchcase(lookup(ins.ip), g) for g in globs)):
                                        hit = disp
                                        break
                                hit[r].setdefault((n, path), (ins.ip, f"{ins}"))
                                counts[(r, n)] += 1
                            else:
                                unclassified[r][n] += 1
    except (ELFError, OSError, ValueError):
        return False
    return True


def load_allow(path):
    """PACKAGE -> list of (features, function globs); no globs covers the package."""
    allow = collections.defaultdict(list)
    for line in open(path):
        line = line.split("#", 1)[0].split()
        if len(line) >= 2:
            feats = {norm(ALIASES.get(norm(f), f)) for f in line[1].split(",") if f}
            globs = [g for g in line[2].split(",") if g] if len(line) > 2 else []
            allow[line[0]].append((feats, globs))
    return allow


def main(argv):
    allow = {}
    if argv[:1] == ["--allow"]:
        allow = load_allow(argv[1])
        argv = argv[2:]
    if len(argv) < 2:
        print(__doc__)
        return 2
    ref_paths = argv[0].split(",")
    refs = [load_reference(p) for p in ref_paths]
    names = feature_names()
    viol = [dict() for _ in refs]
    disp = [dict() for _ in refs]
    unclassified = [collections.Counter() for _ in refs]
    counts = collections.Counter()
    nfiles = 0
    for f in elf_files(argv[1:]):
        if audit_file(f, refs, names, viol, disp, unclassified, counts, allow):
            nfiles += 1
    print(f"audited {nfiles} x86-64 ELF files, {counts['instructions']} instructions")
    for r, path in enumerate(ref_paths):
        have, lack = refs[r]
        print(f"reference {path}: {len(have)} extensions present, {len(lack)} absent")
        sites = {k: ("DISPATCH", v) for k, v in disp[r].items()}
        sites.update({k: ("VIOLATION", v) for k, v in viol[r].items()})
        for (feat, fpath), (kind, (ip, text)) in sorted(sites.items()):
            print(f"{kind}[{r}] {feat} x{counts[(r, feat)]} first in {fpath} at {ip:#x}: {text}")
        for feat, n in unclassified[r].most_common():
            print(f"unclassified[{r}] {feat} x{n}")
        print(f"RESULT[{r}]", "fail" if viol[r] else "pass")
    return 1 if viol[0] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
