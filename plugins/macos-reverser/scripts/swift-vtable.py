#!/usr/bin/env python3
"""List Swift class vtables and override tables from a Mach-O's __swift5_types section.

Each vtable entry shows its byte offset in the class metadata (the #SLOT in
`ldr xN, [xM, #SLOT]` before a `blr`/`br`), its kind and its implementation
address. Override entries show the slot they replace in the superclass. Names
come from the symbol table when the binary is not stripped. Universal binaries
are read from their arm64 slice, or from their first slice.

Usage: swift-vtable.py BINARY
"""

import importlib.util
import os
import shutil
import struct
import subprocess
import sys

_spec = importlib.util.spec_from_file_location(
    "swift_fields", os.path.join(os.path.dirname(os.path.abspath(__file__)), "swift-fields.py"))
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)

LC_SYMTAB = 0x2
KIND_CLASS = 16
GENERIC_FLAG = 0x80
HAS_VTABLE = 1 << 15
HAS_OVERRIDE_TABLE = 1 << 14
HAS_RESILIENT_SUPERCLASS = 1 << 13
METADATA_INIT_SINGLETON, METADATA_INIT_FOREIGN = 1, 2
METHOD_KINDS = {0: "method", 1: "init", 2: "getter", 3: "setter", 4: "modify", 5: "read", 6: "modify2", 7: "read2"}
METHOD_INSTANCE, METHOD_DYNAMIC, METHOD_ASYNC = 0x10, 0x20, 0x40
WORD = 8


class Unsupported(Exception):
    pass


def symbols(image):
    """Map defined symbol addresses to names from LC_SYMTAB."""
    data = image.data
    ncmds = struct.unpack_from("<I", data, 16)[0]
    pos = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, pos)
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, _ = struct.unpack_from("<IIII", data, pos + 8)
            out = {}
            for i in range(nsyms):
                strx, ntype, sect, _, value = struct.unpack_from("<IBBHQ", data, symoff + 16 * i)
                if sect and not ntype & 0xE0 and value not in out:
                    start = stroff + strx
                    out[value] = data[start:data.index(b"\0", start)].decode(errors="replace")
            return out
        pos += cmdsize
    return {}


def demangle(names):
    tool = shutil.which("swift-demangle") or (shutil.which("xcrun") and "xcrun")
    if not names or not tool:
        return {}
    cmd = [tool, "--simplified", "--compact"] if tool != "xcrun" else ["xcrun", "swift-demangle", "--simplified", "--compact"]
    try:
        out = subprocess.run(cmd, input="\n".join(names), capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    lines = out.splitlines()
    return dict(zip(names, lines)) if len(lines) == len(names) else {}


def indirectable(image, vmaddr):
    """Follow a relative indirectable pointer: (target, None) or (None, imported symbol)."""
    delta = image.i32(vmaddr)
    if not delta:
        return None, None
    if delta & 1:
        return image.pointer(vmaddr + (delta & ~1))
    return vmaddr + delta, None


def function(image, impl, flags):
    """An async method's impl is an async function pointer: a relative pointer to the code, then the context size."""
    if impl is None or not flags & METHOD_ASYNC:
        return impl
    return image.relative(impl)


class ClassInfo:
    def __init__(self, image, desc):
        self.desc = desc
        flags = image.u32(desc)
        self.kind_flags = flags >> 16
        name = image.relative(desc + 8)
        self.name = image.cstring(name).decode() if name else "?"
        superclass = image.relative(desc + 20)
        self.superclass = image.type_name(superclass) if superclass else None
        self.vtable_offset = None
        self.methods = []    # (descriptor address, flags, impl or None)
        self.overrides = []  # (base class descriptor or symbol, method descriptor or symbol, impl)
        self.skipped = None
        try:
            self._read_tail(image, flags)
        except Unsupported as e:
            self.skipped = str(e)

    def _read_tail(self, image, flags):
        pos = self.desc + 44
        if flags & GENERIC_FLAG:
            num_params, num_reqs, _, generic_flags = struct.unpack_from("<HHHH", image.data, image.offset(pos + 8))
            if generic_flags:
                raise Unsupported(f"generic context flags {generic_flags:#x}")
            pos += 16 + (num_params + 3) // 4 * 4 + 12 * num_reqs
        kf = self.kind_flags
        if kf & HAS_RESILIENT_SUPERCLASS:
            pos += 4
        init = kf & 3
        if init == METADATA_INIT_FOREIGN:
            pos += 4
        elif init == METADATA_INIT_SINGLETON:
            pos += 12
        if kf & HAS_VTABLE:
            self.vtable_offset, size = image.u32(pos), image.u32(pos + 4)
            pos += 8
            for _ in range(size):
                flags = image.u32(pos)
                self.methods.append((pos, flags, function(image, image.relative(pos + 4), flags)))
                pos += 8
        if kf & HAS_OVERRIDE_TABLE:
            count = image.u32(pos)
            pos += 4
            for _ in range(count):
                cls = indirectable(image, pos)
                method = indirectable(image, pos + 4)
                impl = image.relative(pos + 8)
                if method[0] is not None:
                    impl = function(image, impl, image.u32(method[0]))
                self.overrides.append((cls, method, impl))
                pos += 12


def classes(image):
    if "__swift5_types" not in image.sections:
        raise sf.MachOError("no __swift5_types section (not a Swift binary?)")
    addr, size, _ = image.sections["__swift5_types"]
    for pos in range(addr, addr + size, 4):
        desc = image.relative(pos)
        if desc is None:  # indirect entries name descriptors in other images
            continue
        if image.u32(desc) & 0x1F == KIND_CLASS:
            yield ClassInfo(image, desc)


def method_kind(flags):
    words = [METHOD_KINDS.get(flags & 0xF, f"kind{flags & 0xF}")]
    if not flags & METHOD_INSTANCE:
        words.append("static")
    if flags & METHOD_DYNAMIC:
        words.append("dynamic")
    if flags & METHOD_ASYNC:
        words.append("async")
    return " ".join(words)


def render(image):
    infos = list(classes(image))
    slot_of = {}  # method descriptor address: (class name, byte offset, flags)
    for c in infos:
        if c.vtable_offset is not None:
            for i, (m, flags, _) in enumerate(c.methods):
                slot_of[m] = (c.name, (c.vtable_offset + i) * WORD, flags)
    syms = symbols(image)
    impls = {impl for c in infos for _, _, impl in c.methods + c.overrides if impl}
    names = demangle(sorted({syms[a] for a in impls if a in syms}))

    uses = {}
    for c in infos:
        for _, _, impl in c.methods + c.overrides:
            uses[impl] = uses.get(impl, 0) + 1

    def label(impl):
        if impl is None:
            return "(none: dead-stripped or abstract)"
        sym = syms.get(impl)
        text = f"{impl:#x}  {names.get(sym, sym)}" if sym else f"{impl:#x}"
        return text + ("  [shared]" if uses[impl] > 1 else "")

    lines = []
    for c in infos:
        head = f"class {c.name}" + (f" : {c.superclass}" if c.superclass else "")
        lines.append(f"{head}  (descriptor {c.desc:#x})")
        if c.skipped:
            lines.append(f"  vtable not read: {c.skipped}")
            continue
        for m, flags, impl in c.methods:
            _, slot, _ = slot_of[m]
            lines.append(f"  +{slot:#x}  {method_kind(flags):<14} {label(impl)}")
        for (cls, cls_sym), (method, method_sym), impl in c.overrides:
            if method in slot_of:
                base, slot, flags = slot_of[method]
                lines.append(f"  override +{slot:#x} {base} {method_kind(flags):<14} {label(impl)}")
            else:
                base = names.get(method_sym, method_sym) or (f"{method:#x}" if method else "?")
                lines.append(f"  override {base}  {label(impl)}")
    return lines


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        with open(argv[1], "rb") as f:
            data = f.read()
        offset, size = sf.pick_slice(data)
        print("\n".join(render(sf.Image(data[offset:offset + size]))))
    except (OSError, sf.MachOError, struct.error) as e:
        print(f"swift-vtable: {argv[1]}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
