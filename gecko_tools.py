"""gecko_tools -- the mandatory assemble-and-verify gate for gecko bodies.

History shows hand-encoded PPC hex is this project's #1 time sink (the D.1
mystery, the v1 L-cancel controller bug). Rule: no PPC words reach Dolphin
unless they came out of, or were verified against, this module.

Two entry points:

  words = assemble(SRC)                # keystone, labels resolved for you
  words = assemble_and_verify(SRC, expected=HAND_WORDS)   # + bit-for-bit diff

`assemble_and_verify` also round-trips the result through capstone and fails
on any instruction capstone can't decode (catches valid-length garbage).

keystone needs libkeystone: run with DYLD_LIBRARY_PATH=/opt/homebrew/lib if
the import fails.
"""
import struct

import capstone

try:
    import keystone
except ImportError as e:  # keystone is required for assembling, not for disasm
    keystone = None
    _KEYSTONE_ERR = e


def assemble(src):
    """Assemble PPC32 big-endian source (labels welcome) -> list of word ints."""
    if keystone is None:
        raise RuntimeError(
            f"keystone-engine unavailable ({_KEYSTONE_ERR}); "
            "try DYLD_LIBRARY_PATH=/opt/homebrew/lib")
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(src)
    if len(raw) % 4:
        raise ValueError(f"keystone output length {len(raw)} not word-aligned")
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0]
            for i in range(0, len(raw), 4)]


def disasm(words, addr=0):
    """Capstone-disassemble word ints -> list of 'mnemonic operands' strings.

    Raises ValueError if any word fails to decode (the whole point: garbage
    that happens to be 4 bytes long must not pass silently).
    """
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
    blob = b"".join(struct.pack(">I", w) for w in words)
    out = [f"{i.mnemonic} {i.op_str}".strip() for i in md.disasm(blob, addr)]
    if len(out) != len(words):
        raise ValueError(
            f"capstone decoded {len(out)}/{len(words)} instructions -- "
            "at least one word is not valid PPC")
    return out


def assemble_and_verify(src, expected=None, addr=0):
    """Assemble `src`; verify capstone round-trip; optionally diff vs hand words.

    Returns the assembled word list. Raises with a word-by-word report on any
    mismatch. Use `expected` when a hand-encoded body already exists; omit it
    when this module IS the source of truth.
    """
    words = assemble(src)
    disasm(words, addr)  # raises on undecodable output
    if expected is not None:
        if len(words) != len(expected):
            raise ValueError(
                f"length mismatch: keystone {len(words)} words, "
                f"hand {len(expected)}")
        bad = [(i, k, e) for i, (k, e) in enumerate(zip(words, expected))
               if k != e]
        if bad:
            report = "\n".join(
                f"  idx {i}: keystone=0x{k:08X} hand=0x{e:08X}" for i, k, e in bad)
            raise ValueError(f"{len(bad)} word mismatch(es):\n{report}")
    return words


def check_c2_body(words):
    """Enforce the C2-codehandler rule: the handler OVERWRITES the body's last
    word with its branch-back, so the last word must be a throwaway 0x00000000
    (and the count even). See the 2026-05-21 v1 L-cancel ship bug.
    """
    if len(words) % 2:
        raise ValueError("C2 body word count must be even (pad with nop)")
    if words[-1] != 0:
        raise ValueError(
            f"C2 body's last word is 0x{words[-1]:08X}, not a throwaway 0 -- "
            "the codehandler will EAT it (branch-back overwrite)")


if __name__ == "__main__":
    # Self-check: known encoding + a deliberate mismatch must be caught.
    src = "lis 12, 0x803F\nori 12, 12, 0xA424\nlbz 9, 0(12)\n"
    words = assemble_and_verify(src, expected=[0x3D80803F, 0x618CA424, 0x892C0000])
    assert disasm(words)[0].startswith("lis")
    try:
        assemble_and_verify(src, expected=[0x3D80803F, 0x618CA424, 0x896C0000])
        raise SystemExit("FAIL: mismatch not caught")
    except ValueError:
        pass
    try:
        check_c2_body([0x60000000, 0x540084BE])
        raise SystemExit("FAIL: eaten-last-word not caught")
    except ValueError:
        pass
    check_c2_body([0x540084BE, 0x60000000, 0x4E800020, 0x00000000])
    print("[PASS] gecko_tools self-check")
