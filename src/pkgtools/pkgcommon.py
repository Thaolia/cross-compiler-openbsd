"""Helpers shared by pkg_create.py and pkg_fetch.py."""
import gzip
import io
import os
import re
import stat
import tarfile

LIB_RE = re.compile(r"^lib(?P<stem>.+)\.so\.(?P<major>\d+)\.(?P<minor>\d+)$")
PKGNAME_RE = re.compile(r"^(?P<stem>.+?)-(?P<version>\d[^-]*)(?P<flavor>(-[A-Za-z0-9_]+)*)$")
# (uname, gname, uid, gid) used by OpenBSD's pkg_create for payload and metadata.
PAYLOAD_OWNER = ("root", "bin", 0, 7)
META_OWNER = ("root", "wheel", 0, 0)


def mtree_dirs(path):
    """Parse an mtree(8) spec made only of directories; return the set of absolute paths."""
    dirs = set()
    stack = []
    with open(path) as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("/"):
                continue
            name = line.split()[0]
            if name == "..":
                stack.pop()
                continue
            if "type=" in line and "type=dir" not in line:
                continue
            stack.append(name)
            dirs.add("/" + "/".join(stack[1:]) if len(stack) > 1 else "/")
    return dirs


def split_pkgname(name):
    m = PKGNAME_RE.match(name)
    if not m:
        raise ValueError(f"invalid package name '{name}' (expected stem-version[-flavor])")
    return m.group("stem"), m.group("version"), m.group("flavor")


def parse_plist(text):
    """Yield (keyword, argument) for each line of a +CONTENTS; keyword is None for plain files."""
    for line in text.splitlines():
        if line.startswith("@"):
            kw, _, arg = line[1:].partition(" ")
            yield kw, arg
        elif line:
            yield None, line


def read_signed_tgz(data):
    """Return a TarFile reading the (possibly signed) gzip'd package bytes."""
    return tarfile.open(fileobj=io.BytesIO(gzip.decompress(data)), mode="r:")


def _octal(value, width):
    return b"%0*o\0" % (width - 1, value)


def ustar_header(name, size=0, mode=0o644, mtime=0, symlink=None, owner=PAYLOAD_OWNER,
                 typebits=True):
    """One 512-byte ustar header laid out like OpenBSD's pkg_create writes it."""
    uname, gname, uid, gid = owner
    raw = name.encode()
    prefix = b""
    if len(raw) > 100:
        cut = raw.rfind(b"/", 0, 156)
        if cut <= 0 or len(raw) - cut - 1 > 100:
            raise ValueError(f"path too long for ustar: {name}")
        prefix, raw = raw[:cut], raw[cut + 1:]
    link = (symlink or "").encode()
    if len(link) > 100:
        raise ValueError(f"symlink target too long for ustar: {symlink}")
    # pkg_create sets S_IF* bits in the mode of payload members, not of +CONTENTS/+DESC.
    ftype = 0 if not typebits else stat.S_IFLNK if symlink is not None else stat.S_IFREG
    h = bytearray(512)
    h[0:len(raw)] = raw
    h[100:108] = _octal(ftype | mode, 8)
    h[108:116] = _octal(uid, 8)
    h[116:124] = _octal(gid, 8)
    h[124:136] = _octal(0 if symlink is not None else size, 12)
    h[136:148] = _octal(mtime, 12)
    h[148:156] = b" " * 8
    h[156:157] = b"2" if symlink is not None else b"0"
    h[157:157 + len(link)] = link
    h[257:265] = b"ustar\x0000"
    h[265:265 + len(uname)] = uname.encode()
    h[297:297 + len(gname)] = gname.encode()
    h[329:337] = _octal(0, 8)
    h[337:345] = _octal(0, 8)
    h[345:345 + len(prefix)] = prefix
    h[148:156] = b"%06o\0 " % sum(h)
    return bytes(h)


def ustar_member(name, data=b"", **kw):
    """Header plus data padded to a 512-byte boundary."""
    return ustar_header(name, size=len(data), **kw) + data + b"\0" * (-len(data) % 512)


USTAR_END = b"\0" * 1024


def safe_member_path(root, name):
    """Join an archive member name to root, refusing absolute paths and '..'."""
    if name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"unsafe path in package: {name}")
    return os.path.join(root, name)
