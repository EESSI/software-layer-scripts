"""Audit aarch64 ELF files against a CPU target with llvm-objdump.

Each file is disassembled twice: once with every feature llvm-objdump knows
(its default for AArch64), and once restricted to the target. An instruction
that decodes in the first and shows as <unknown> in the second needs a feature
the target lacks: a violation.

The target comes from a software-layer-scripts#311 reference, in either form:
  clang:  "-target-cpu X" and "-target-feature +f" lines
  gcc:    one "-mcpu=X+f+nof..." line

    python isa_audit_aarch64.py REFERENCE PATH [PATH ...]

llvm-objdump must be on PATH (LLVM 20 or later). Exit status 1 on violation.
"""
import collections
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
    out = subprocess.run(["llvm-objdump", "-d"] + extra + [path],
                         capture_output=True, text=True)
    insns = {}
    for line in out.stdout.splitlines():
        m = LINE.match(line)
        if m:
            insns[m.group(1)] = (m.group(2), m.group(3).strip())
    return insns


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
    if len(argv) < 2:
        print(__doc__)
        return 2
    restrict = target_args(argv[0])
    viol, first = collections.Counter(), {}
    nfiles = ninsn = 0
    for f in files(argv[1:]):
        if not is_aarch64_elf(f):
            continue
        full = disassemble(f, [])
        narrow = disassemble(f, restrict)
        nfiles += 1
        ninsn += len(full)
        for addr, (mn, ops) in full.items():
            if mn == "<unknown>":
                continue
            if narrow.get(addr, ("<unknown>", ""))[0] == "<unknown>":
                viol[mn] += 1
                first.setdefault(mn, (f, addr, ops))
    print(f"target {' '.join(restrict)}")
    print(f"audited {nfiles} aarch64 ELF files, {ninsn} instructions")
    for mn, n in viol.most_common(15):
        f, addr, ops = first[mn]
        print(f"VIOLATION {mn} x{n} first in {f} at 0x{addr}: {mn} {ops}")
    print(f"violations {sum(viol.values())} in {len(viol)} mnemonics")
    print("RESULT", "fail" if viol else "pass")
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
