"""Minimal ELF64 little-endian reader: file kind, DT_NEEDED and DT_SONAME.

Just enough to classify package files (@bin / @lib) and compute @wantlib,
without depending on pyelftools.
"""
import struct

ELF_MAGIC = b"\x7fELF"
ET_EXEC, ET_DYN = 2, 3
PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
DT_NULL, DT_NEEDED, DT_STRTAB, DT_SONAME = 0, 1, 5, 14


class ElfInfo:
    __slots__ = ("etype", "has_interp", "needed", "soname")

    def __init__(self, etype, has_interp, needed, soname):
        self.etype = etype
        self.has_interp = has_interp
        self.needed = needed
        self.soname = soname

    @property
    def is_executable(self):
        return self.etype == ET_EXEC or (self.etype == ET_DYN and self.has_interp)

    @property
    def is_shared_lib(self):
        return self.etype == ET_DYN and not self.has_interp


def read(path):
    """Return ElfInfo for an ELF64 LE file, or None if the file is not ELF."""
    with open(path, "rb") as f:
        head = f.read(64)
        if len(head) < 64 or head[:4] != ELF_MAGIC:
            return None
        if head[4] != 2 or head[5] != 1:
            raise ValueError(f"{path}: only ELF64 little-endian is supported")
        f.seek(0)
        data = f.read()

    etype = struct.unpack_from("<H", data, 16)[0]
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)

    loads = []
    dynamic = None
    has_interp = False
    for i in range(phnum):
        p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + i * phentsize)
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
        elif p_type == PT_INTERP:
            has_interp = True

    if dynamic is None:
        return ElfInfo(etype, has_interp, [], None)

    def vaddr_to_off(addr):
        for vaddr, off, size in loads:
            if vaddr <= addr < vaddr + size:
                return off + (addr - vaddr)
        raise ValueError(f"{path}: address {addr:#x} not in any PT_LOAD")

    entries = []
    strtab = None
    off, size = dynamic
    for o in range(off, off + size, 16):
        tag, val = struct.unpack_from("<qQ", data, o)
        if tag == DT_NULL:
            break
        if tag == DT_STRTAB:
            strtab = vaddr_to_off(val)
        entries.append((tag, val))
    if strtab is None:
        raise ValueError(f"{path}: PT_DYNAMIC without DT_STRTAB")

    def string(idx):
        end = data.index(b"\0", strtab + idx)
        return data[strtab + idx:end].decode()

    needed = [string(v) for t, v in entries if t == DT_NEEDED]
    sonames = [string(v) for t, v in entries if t == DT_SONAME]
    return ElfInfo(etype, has_interp, needed, sonames[0] if sonames else None)
