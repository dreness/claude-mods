#!/usr/bin/env python3
# ABOUTME: Lists Swift types and stored fields (let/var) from a Mach-O's field metadata.
# ABOUTME: Reads thin or universal binaries without external tools.
"""List Swift types and their stored fields from a Mach-O's __swift5_fieldmd section.

Each field shows whether it is a `let` (only writable in an initializer) or a
`var`, which tells you whether a store outside init can exist at all. Enum
cases show as `case`. Universal binaries are read from their arm64 slice.

Usage: swift-fields.py BINARY
"""

import struct
import sys

FAT_MAGICS = {0xCAFEBABE: False, 0xCAFEBABF: True}  # value: 64-bit fat_arch
MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
LC_SEGMENT_64 = 0x19
KINDS = {0: "struct", 1: "class", 2: "enum", 3: "enum", 7: "class"}
ENUM_KINDS = {2, 3}
FIELD_IS_VAR = 0x2


class MachOError(Exception):
    pass


def arm64_slice(data):
    """Return (offset, size) of the arm64 slice, or of the whole file if thin."""
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic not in FAT_MAGICS:
        return 0, len(data)
    is64 = FAT_MAGICS[magic]
    count = struct.unpack_from(">I", data, 4)[0]
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
    raise MachOError("universal binary has no arm64 slice")


class Image:
    """One thin Mach-O image, addressed by VM address."""

    def __init__(self, data):
        self.data = data
        if struct.unpack_from("<I", data, 0)[0] != MH_MAGIC_64:
            raise MachOError("not a 64-bit Mach-O")
        ncmds = struct.unpack_from("<I", data, 16)[0]
        self.segments = []  # (vmaddr, vmsize, fileoff)
        self.sections = {}  # name: (addr, size, offset)
        pos = 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", data, pos)
            if cmd == LC_SEGMENT_64:
                vmaddr, vmsize, fileoff = struct.unpack_from("<QQQ", data, pos + 24)
                self.segments.append((vmaddr, vmsize, fileoff))
                nsects = struct.unpack_from("<I", data, pos + 64)[0]
                sect = pos + 72
                for _ in range(nsects):
                    name = data[sect:sect + 16].rstrip(b"\0").decode()
                    addr, size, offset = struct.unpack_from("<QQI", data, sect + 32)
                    self.sections[name] = (addr, size, offset)
                    sect += 80
            pos += cmdsize

    def offset(self, vmaddr):
        for start, size, fileoff in self.segments:
            if start <= vmaddr < start + size:
                return fileoff + vmaddr - start
        raise MachOError(f"address {vmaddr:#x} is outside every segment")

    def i32(self, vmaddr):
        return struct.unpack_from("<i", self.data, self.offset(vmaddr))[0]

    def u32(self, vmaddr):
        return struct.unpack_from("<I", self.data, self.offset(vmaddr))[0]

    def relative(self, vmaddr):
        """Follow a 32-bit relative pointer; None when it is null."""
        delta = self.i32(vmaddr)
        return vmaddr + delta if delta else None

    def cstring(self, vmaddr):
        start = self.offset(vmaddr)
        return self.data[start:self.data.index(b"\0", start)]

    def type_name(self, vmaddr):
        """Name a type from its mangled-name reference, resolving direct symbolic references."""
        raw = self.cstring(vmaddr)
        if raw[:1] == b"\x01":
            descriptor = vmaddr + 1 + self.i32(vmaddr + 1)
            return self.cstring(self.relative(descriptor + 8)).decode()
        if raw and all(0x20 <= b < 0x7F for b in raw):
            return raw.decode()
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
        yield kind, image.type_name(type_ref) if type_ref else "?", fields
        pos += 16 + count * record_size


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        with open(argv[1], "rb") as f:
            data = f.read()
        offset, size = arm64_slice(data)
        image = Image(data[offset:offset + size])
        for kind, name, fields in field_descriptors(image):
            if not fields:
                continue
            print(f"{KINDS.get(kind, f'kind{kind}')} {name}")
            for flags, field in fields:
                keyword = "case" if kind in ENUM_KINDS else ("var" if flags & FIELD_IS_VAR else "let")
                print(f"  {keyword} {field}")
    except (OSError, MachOError, struct.error) as e:
        print(f"swift-fields: {argv[1]}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
