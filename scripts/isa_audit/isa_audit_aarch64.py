"""Audit aarch64 ELF files against a CPU target with llvm-objdump.

Each file is disassembled twice: once with every feature llvm-objdump knows
(its default for AArch64), and once restricted to the target. An instruction
that decodes in the first and shows as <unknown> in the second needs a feature
the target lacks: a violation.

The target comes from a software-layer-scripts#311 reference, in either form:
  clang:  "-target-cpu X" and "-target-feature +f" lines
  gcc:    one "-mcpu=X+f+nof..." line

    python isa_audit_aarch64.py [--allow FILE] REFERENCE PATH [PATH ...]

llvm-objdump must be on PATH (LLVM 20 or later). Exit status 1 on violation.

The helpers libgcc links for -moutline-atomics (__aarch64_ldadd8_acq_rel and
friends) carry LSE instructions behind a check of HWCAP_ATOMICS, so
instructions inside those functions pass by rule. In a stripped file the
helpers have no names; they are recognised by their shape instead: the LSE
instruction directly follows "ldrb w16, [__aarch64_have_lse_atomics]" and
"cbz w16".

Each violation is attributed to the LLVM feature that makes it decode when
added to the target. --allow FILE takes the same "PACKAGE feature[,...]
[FUNCTION[,...]]  # reason" lines as isa_audit.py, with LLVM feature names: a
violation in a file under /software/PACKAGE/ on a listed feature, and inside a
matching function when globs are given, is reported as DISPATCH.
"""
import collections
import fnmatch
import os
import re
import subprocess
import sys

#: GCC extension name -> LLVM feature name, where they differ
GCC_TO_LLVM = {
    "rng": "rand",
    "crypto": "crypto",
    "sve2-aes": "sve-aes",
    "sve2-sha3": "sve-sha3",
    "sve2-sm4": "sve-sm4",
    "sve2-bitperm": "sve-bitperm",
    "memtag": "mte",
    "profile": "spe",
    "fp16": "fullfp16",
}

LINE = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(\S+)\s*(.*)$")

#: clang -target-feature entries that are code-generation options, not ISA
NOT_ISA = {"outline-atomics", "fmv"}

#: function label line in llvm-objdump -d output
LABEL = re.compile(r"^([0-9a-f]+) <(.+)>:$")

#: libgcc's -moutline-atomics helpers, which use LSE only when HWCAP_ATOMICS is set
OUTLINE_ATOMICS = re.compile(r"^__aarch64_(cas|casp|swp|ldadd|ldclr|ldeor|ldset)(1|2|4|8|16)_(relax|acq|rel|acq_rel|sync)$")

#: LLVM features tried, in order, to name the feature a violation needs;
#: both the LLVM 19 (sve2-*) and LLVM 20 (sve-*) names of the SVE2 crypto features
CANDIDATES = [
    "crc", "lse", "rdm", "rcpc", "rcpc-immo", "dotprod", "fullfp16", "fp16fml",
    "aes", "sha2", "sha3", "sm4", "jsconv", "complxnum", "flagm", "pauth", "rand",
    "ssbs", "sb", "predres", "frintts", "altnzcv", "i8mm", "bf16", "mte",
    "sve", "sve2", "sve-aes", "sve2-aes", "sve-sha3", "sve2-sha3", "sve-sm4",
    "sve2-sm4", "sve-bitperm", "sve2-bitperm", "ls64", "sme", "lse128", "rcpc3",
]


def target_args(path):
    cpu, feats = None, []
    for raw in open(path):
        tok = raw.strip()
        if tok.startswith("-target-cpu "):
            cpu = tok.split()[1]
        elif tok.startswith("-target-feature "):
            f = tok.split()[1]
            if f[1:] not in NOT_ISA:
                feats.append(f)
        elif tok.startswith("-mcpu="):
            parts = tok[len("-mcpu="):].split("+")
            cpu = parts[0]
            for p in parts[1:]:
                if p.startswith("no"):
                    feats.append("-" + GCC_TO_LLVM.get(p[2:], p[2:]))
                else:
                    feats.append("+" + GCC_TO_LLVM.get(p, p))
    if cpu is None:
        raise SystemExit(f"{path}: no -target-cpu or -mcpu= line")
    # GCC's internal core names for some parts
    cpu = {"zeus": "neoverse-v1", "ares": "neoverse-n1"}.get(cpu, cpu)
    args = [f"--mcpu={cpu}"]
    if feats:
        args.append("--mattr=" + ",".join(feats))
    return args


def disassemble(path, extra):
    """Yield (address, mnemonic, operands, enclosing function), streamed so
    that large libraries are never held in memory whole."""
    proc = subprocess.Popen(["llvm-objdump", "-d"] + extra + [path],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    func = ""
    for line in proc.stdout:
        lab = LABEL.match(line.rstrip("\n"))
        if lab:
            func = lab.group(2)
            continue
        m = LINE.match(line)
        if m:
            yield m.group(1), m.group(2), m.group(3).strip(), func
    proc.wait()


def with_feature(restrict, feature):
    """The target's llvm-objdump arguments with one more feature."""
    args = list(restrict)
    for i, a in enumerate(args):
        if a.startswith("--mattr="):
            args[i] = a + ",+" + feature
            return args
    return args + ["--mattr=+" + feature]


def attribute(path, restrict, addrs):
    """Name the first candidate feature that makes each address decode."""
    found, left = {}, set(addrs)
    for feat in CANDIDATES:
        if not left:
            break
        for a, mn, _, _ in disassemble(path, with_feature(restrict, feat)):
            if a in left and mn != "<unknown>":
                found[a] = feat
                left.discard(a)
    return found


def load_allow(path):
    """PACKAGE -> list of (features, function globs); no globs covers the package."""
    allow = collections.defaultdict(list)
    for line in open(path):
        line = line.split("#", 1)[0].split()
        if len(line) >= 2:
            feats = set(f for f in line[1].split(",") if f)
            globs = [g for g in line[2].split(",") if g] if len(line) > 2 else []
            allow[line[0]].append((feats, globs))
    return allow


def allowed(path, feat, func, allow):
    return any(f"/software/{pkg}/" in path and feat in feats
               and (not globs or any(fnmatch.fnmatchcase(func, g) for g in globs))
               for pkg, entries in allow.items() for feats, globs in entries)


def is_aarch64_elf(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(20)
    except OSError:
        return False
    return head[:4] == b"\x7fELF" and head[18:20] == b"\xb7\x00"


def files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in names:
                    full = os.path.join(root, n)
                    if os.path.islink(full) or not os.path.isfile(full):
                        continue
                    if ".so" in n or os.access(full, os.X_OK):
                        yield full
        else:
            yield p


def main(argv):
    allow = {}
    if argv[:1] == ["--allow"]:
        allow = load_allow(argv[1])
        argv = argv[2:]
    if len(argv) < 2:
        print(__doc__)
        return 2
    restrict = target_args(argv[0])
    viol, disp, first = collections.Counter(), collections.Counter(), {}
    nfiles = ninsn = natomics = 0
    for f in files(argv[1:]):
        if not is_aarch64_elf(f):
            continue
        unknown = {a for a, mn, _, _ in disassemble(f, restrict) if mn == "<unknown>"}
        nfiles += 1
        full, bad = {}, []
        prev = [("", ""), ("", "")]
        for addr, mn, ops, func in disassemble(f, []):
            ninsn += 1
            guarded = prev[0][0] == "ldrb" and prev[0][1].startswith("w16,") \
                and prev[1][0] == "cbz" and prev[1][1].startswith("w16,")
            prev = [prev[1], (mn, ops)]
            if mn == "<unknown>" or addr not in unknown:
                continue
            if OUTLINE_ATOMICS.match(func) or guarded:
                natomics += 1
            else:
                full[addr] = (mn, ops, func)
                bad.append(addr)
        del unknown
        if not bad:
            continue
        feats = attribute(f, restrict, bad)
        for addr in bad:
            mn, ops, func = full[addr]
            key = (feats.get(addr, "unknown"), mn)
            target = disp if allowed(f, key[0], func, allow) else viol
            target[key] += 1
            first.setdefault(key, (f, addr, ops, func))
    print(f"target {' '.join(restrict)}")
    print(f"audited {nfiles} aarch64 ELF files, {ninsn} instructions")
    if natomics:
        print(f"outline-atomics {natomics} LSE instructions in libgcc -moutline-atomics helpers, passed by rule")
    for kind, cnt in (("DISPATCH", disp), ("VIOLATION", viol)):
        for (feat, mn), n in cnt.most_common(15):
            f, addr, ops, func = first[(feat, mn)]
            print(f"{kind} {feat} {mn} x{n} first in {f} at 0x{addr} <{func}>: {mn} {ops}")
    print(f"violations {sum(viol.values())} in {len(viol)} mnemonics")
    print("RESULT", "fail" if viol else "pass")
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
