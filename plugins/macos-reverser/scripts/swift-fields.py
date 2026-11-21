#!/usr/bin/env python3
"""List Swift types and their stored fields from a Mach-O's __swift5_fieldmd section.

Each field shows whether it is a `let` (only writable in an initializer) or a
`var`, which tells you whether a store outside init can exist at all. Enum
cases show as `case`. Classes show their superclass and are listed even with no
stored fields. Universal binaries are read from their arm64 slice, or
from their first slice when there is no arm64 one.

Usage: swift-fields.py BINARY
"""

import struct
import sys

FAT_MAGICS = {0xCAFEBABE: False, 0xCAFEBABF: True}  # value: 64-bit fat_arch
MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
LC_SEGMENT_64 = 0x19
LC_DYLD_CHAINED_FIXUPS = 0x80000034
KINDS = {0: "struct", 1: "class", 2: "enum", 3: "enum", 7: "class"}
ENUM_KINDS = {2, 3}
FIELD_IS_VAR = 0x2
SYMBOLIC_DIRECT = 0x01
SYMBOLIC_INDIRECT = 0x02
# Chained pointer formats; ARM64E ones carry auth and bind flags in bits 63 and 62.
PTR_ARM64E, PTR_64, PTR_64_OFFSET, PTR_ARM64E_USERLAND, PTR_ARM64E_USERLAND24 = 1, 2, 6, 9, 12
STDLIB_TYPES = {
    "Sa": "Array", "Sb": "Bool", "SD": "Dictionary", "Sd": "Double", "Sf": "Float",
    "Sh": "Set", "Si": "Int", "Sq": "Optional", "SS": "String", "Su": "UInt",
}


class MachOError(Exception):
    pass


def pick_slice(data):
    """Return (offset, size) of the arm64 slice, else the first slice, or the whole file if thin."""
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic not in FAT_MAGICS:
        return 0, len(data)
    is64 = FAT_MAGICS[magic]
    count = struct.unpack_from(">I", data, 4)[0]
    if count == 0:
        raise MachOError("universal binary has no slices")
    slices = []
    pos = 8
    for _ in range(count):
        if is64:
            cputype, _, offset, size = struct.unpack_from(">iiQQ", data, pos)
            pos += 32
        else:
            cputype, _, offset, size = struct.unpack_from(">iiII", data, pos)
            pos += 20
        if cputype == CPU_TYPE_ARM64:
            return offset, size
        slices.append((offset, size))
    return slices[0]


MAX_WORDS = 26


def add_words(words, text):
    """Record the words of a literal identifier part, as the demangler does, for later word substitutions."""
    start = -1
    for i in range(len(text) + 1):
        c = text[i] if i < len(text) else ""
        if start >= 0 and (c in ("_", "") or (not text[i - 1].isupper() and c.isupper())):
            if i - start >= 2 and len(words) < MAX_WORDS:
                words.append(text[start:i])
            start = -1
        if start < 0 and c and not c.isdigit() and c != "_":
            start = i


def read_natural(mangled, pos):
    end = pos
    while end < len(mangled) and mangled[end].isdigit():
        end += 1
    return (int(mangled[pos:end]), end) if end > pos else (None, pos)


def read_identifier(mangled, pos, words):
    """Decode one identifier at pos: (name, new pos), or (None, pos) if there is none.

    `0` starts an identifier with word substitutions: lowercase letters a-z pick earlier
    words and an uppercase letter picks the last one, mixed with literal NATURAL+text parts.
    """
    if pos >= len(mangled) or not mangled[pos].isdigit():
        return None, pos
    subst = mangled[pos] == "0"
    if subst:
        pos += 1
        if mangled[pos:pos + 1] == "0":  # punycode
            return None, pos
    name = ""
    while True:
        while subst and pos < len(mangled) and mangled[pos].isalpha():
            c = mangled[pos]
            index = ord(c.lower()) - ord("a")
            if index >= len(words):
                return None, pos
            name += words[index]
            subst = c.islower()
            pos += 1
        if mangled[pos:pos + 1] == "0":
            pos += 1
            break
        length, end = read_natural(mangled, pos)
        if not length or end + length > len(mangled):
            if name and not subst:  # identifier ended with an uppercase substitution
                break
            return None, pos
        part = mangled[end:end + length]
        add_words(words, part)
        name, pos = name + part, end + length
        if not subst:
            break
    return name, pos


def nominal_name(mangled):
    """Innermost name in a plain nominal type mangling such as 10Foundation4DateV; None for anything else."""
    if mangled in STDLIB_TYPES:
        return STDLIB_TYPES[mangled]
    pos = 2 if mangled.startswith("So") else 0
    name, words = None, []
    while pos < len(mangled):
        c = mangled[pos]
        if c.isdigit():
            ident, pos = read_identifier(mangled, pos, words)
            if ident is None:
                return None
            name = ident
        elif c in "VCO" and name:
            pos += 1
        else:
            return None
    return name


def symbol_type_name(symbol):
    """Name the type behind a nominal type descriptor symbol such as _$s10Foundation4DateVMn."""
    mangled = symbol.removeprefix("_").removeprefix("$s").removesuffix("Mn")
    return nominal_name(mangled) or symbol


class Image:
    """One thin Mach-O image, addressed by VM address."""

    def __init__(self, data):
        self.data = data
        if struct.unpack_from("<I", data, 0)[0] != MH_MAGIC_64:
            raise MachOError("not a 64-bit Mach-O")
        ncmds = struct.unpack_from("<I", data, 16)[0]
        self.segments = []  # (name, vmaddr, vmsize, fileoff)
        self.sections = {}  # name: (addr, size, offset)
        fixups = None
        pos = 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", data, pos)
            if cmd == LC_SEGMENT_64:
                segname = data[pos + 8:pos + 24].rstrip(b"\0").decode()
                vmaddr, vmsize, fileoff = struct.unpack_from("<QQQ", data, pos + 24)
                self.segments.append((segname, vmaddr, vmsize, fileoff))
                nsects = struct.unpack_from("<I", data, pos + 64)[0]
                sect = pos + 72
                for _ in range(nsects):
                    name = data[sect:sect + 16].rstrip(b"\0").decode()
                    addr, size, offset = struct.unpack_from("<QQI", data, sect + 32)
                    self.sections[name] = (addr, size, offset)
                    sect += 80
            elif cmd == LC_DYLD_CHAINED_FIXUPS:
                fixups = struct.unpack_from("<I", data, pos + 8)[0]
            pos += cmdsize
        self.base = next((s[1] for s in self.segments if s[0] == "__TEXT"), 0)
        self.pointer_formats = {}  # segment index: chained pointer format
        self.imports = []
        if fixups is not None:
            self._read_chained_fixups(fixups)

    def _read_chained_fixups(self, header):
        data = self.data
        (_, starts, imports, symbols, import_count, import_format,
         symbol_format) = struct.unpack_from("<7I", data, header)
        starts += header
        for i in range(struct.unpack_from("<I", data, starts)[0]):
            info = struct.unpack_from("<I", data, starts + 4 + 4 * i)[0]
            if info:
                self.pointer_formats[i] = struct.unpack_from("<H", data, starts + info + 6)[0]
        if symbol_format != 0:  # zlib-compressed names
            return
        stride, unpack, shift = {1: (4, "<I", 9), 2: (8, "<I", 9), 3: (16, "<Q", 32)}[import_format]
        for i in range(import_count):
            name_offset = struct.unpack_from(unpack, data, header + imports + i * stride)[0] >> shift
            start = header + symbols + name_offset
            self.imports.append(data[start:data.index(b"\0", start)].decode())

    def segment_index(self, vmaddr):
        for i, (_, start, size, _) in enumerate(self.segments):
            if start <= vmaddr < start + size:
                return i
        raise MachOError(f"address {vmaddr:#x} is outside every segment")

    def offset(self, vmaddr):
        _, start, _, fileoff = self.segments[self.segment_index(vmaddr)]
        return fileoff + vmaddr - start

    def i32(self, vmaddr):
        return struct.unpack_from("<i", self.data, self.offset(vmaddr))[0]

    def u32(self, vmaddr):
        return struct.unpack_from("<I", self.data, self.offset(vmaddr))[0]

    def relative(self, vmaddr):
        """Follow a 32-bit relative pointer; None when it is null."""
        delta = self.i32(vmaddr)
        return vmaddr + delta if delta else None

    def pointer(self, vmaddr):
        """Read a stored pointer as (target vmaddr, None) or (None, imported symbol name).

        Without chained fixups a rebase is stored as its unslid address and a bind as 0.
        """
        raw = struct.unpack_from("<Q", self.data, self.offset(vmaddr))[0]
        fmt = self.pointer_formats.get(self.segment_index(vmaddr))
        if fmt is None:
            return raw or None, None
        if fmt in (PTR_ARM64E, PTR_ARM64E_USERLAND, PTR_ARM64E_USERLAND24):
            auth, bind = raw >> 63, raw >> 62 & 1
            if bind:
                return None, self.import_name(raw & (0xFFFFFF if fmt == PTR_ARM64E_USERLAND24 else 0xFFFF))
            if auth:
                return self.base + (raw & 0xFFFFFFFF), None
            target = raw & ((1 << 43) - 1)
            return (target if fmt == PTR_ARM64E else self.base + target), None
        if fmt in (PTR_64, PTR_64_OFFSET):
            if raw >> 63:
                return None, self.import_name(raw & 0xFFFFFF)
            target = raw & ((1 << 36) - 1)
            return (target if fmt == PTR_64 else self.base + target), None
        raise MachOError(f"unsupported chained pointer format {fmt}")

    def import_name(self, ordinal):
        return self.imports[ordinal] if ordinal < len(self.imports) else None

    def cstring(self, vmaddr):
        start = self.offset(vmaddr)
        return self.data[start:self.data.index(b"\0", start)]

    def type_name(self, vmaddr):
        """Name a type from its mangled-name reference, resolving a leading symbolic reference."""
        kind = self.data[self.offset(vmaddr)]
        if kind in (SYMBOLIC_DIRECT, SYMBOLIC_INDIRECT):
            descriptor = vmaddr + 1 + self.i32(vmaddr + 1)
            if kind == SYMBOLIC_INDIRECT:
                descriptor, symbol = self.pointer(descriptor)
                if symbol:
                    return symbol_type_name(symbol)
            return self.cstring(self.relative(descriptor + 8)).decode() if descriptor else "?"
        raw = self.cstring(vmaddr)
        if raw and all(0x20 <= b < 0x7F for b in raw):
            return nominal_name(raw.decode()) or raw.decode()
        return "?"


def field_descriptors(image):
    if "__swift5_fieldmd" not in image.sections:
        raise MachOError("no __swift5_fieldmd section (not a Swift binary?)")
    addr, size, _ = image.sections["__swift5_fieldmd"]
    pos, end = addr, addr + size
    while pos < end:
        type_ref = image.relative(pos)
        kind, record_size = struct.unpack_from("<HH", image.data, image.offset(pos + 8))
        count = image.u32(pos + 12)
        fields = []
        for i in range(count):
            record = pos + 16 + i * record_size
            flags = image.u32(record)
            name = image.relative(record + 8)
            fields.append((flags, image.cstring(name).decode() if name else "?"))
        superclass = image.relative(pos + 4)
        name = image.type_name(type_ref) if type_ref else "?"
        yield kind, name, image.type_name(superclass) if superclass else None, fields
        pos += 16 + count * record_size


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        with open(argv[1], "rb") as f:
            data = f.read()
        offset, size = pick_slice(data)
        image = Image(data[offset:offset + size])
        for kind, name, superclass, fields in field_descriptors(image):
            is_class = KINDS.get(kind) == "class"
            if not fields and not is_class:
                continue
            print(f"{KINDS.get(kind, f'kind{kind}')} {name}" + (f" : {superclass}" if is_class and superclass else ""))
            for flags, field in fields:
                keyword = "case" if kind in ENUM_KINDS else ("var" if flags & FIELD_IS_VAR else "let")
                print(f"  {keyword} {field}")
    except (OSError, MachOError, struct.error) as e:
        print(f"swift-fields: {argv[1]}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
