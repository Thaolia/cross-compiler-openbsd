#!/usr/bin/env python3
"""Create an OpenBSD binary package (.tgz) from a staged install tree, on any host.

    make install DESTDIR=$PWD/stage
    pkg_create.py --sysroot <sysroot> --name foo-1.0 --pkgpath misc/foo \\
        --comment "one line" --desc-file DESCR -o foo-1.0.tgz stage/usr/local

The staged directory holds the files relative to --prefix (default /usr/local).
@bin/@lib/@static-lib/@man annotations, @wantlib and the @depend lines for libraries
coming from packages installed in the sysroot (pkg_fetch.py) are computed automatically.
"""
import argparse
import base64
import gzip
import hashlib
import os
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import elfinfo  # noqa: E402
import pkgcommon  # noqa: E402
import signify  # noqa: E402

SYSTEM_LIB_DIRS = ("usr/lib", "usr/X11R6/lib")


class PkgError(Exception):
    pass


def sha_b64(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def installed_libs(sysroot):
    """Map 'libfoo.so.M.m' -> (pkgname, pkgpath) for packages registered in the sysroot."""
    result = {}
    dbdir = os.path.join(sysroot, "var/db/pkg")
    if not os.path.isdir(dbdir):
        return result
    for pkg in sorted(os.listdir(dbdir)):
        contents = os.path.join(dbdir, pkg, "+CONTENTS")
        if not os.path.isfile(contents):
            continue
        with open(contents) as f:
            plist = list(pkgcommon.parse_plist(f.read()))
        name = next((a for k, a in plist if k == "name"), pkg)
        pkgpath = next((a.split()[0].split("=", 1)[1] for k, a in plist
                        if k == "comment" and a.startswith("pkgpath=")), None)
        cwd = "/usr/local"
        for kw, arg in plist:
            if kw == "cwd":
                cwd = arg
            elif kw == "lib" and cwd == "/usr/local":
                result[os.path.basename(arg)] = (name, pkgpath)
    return result


class PackageBuilder:
    def __init__(self, args):
        self.a = args
        self.stage = os.path.abspath(args.stage)
        self.sysroot = os.path.abspath(args.sysroot)
        self.prefix = args.prefix.rstrip("/") or "/"
        mtree = os.path.join(self.sysroot, "etc/mtree/4.4BSD.dist")
        if not os.path.isfile(mtree):
            raise PkgError(f"{mtree} missing: is {self.sysroot} a sysroot from fetch-sysroot.sh?")
        self.std_dirs = pkgcommon.mtree_dirs(mtree)
        self.entries = []      # plist lines for the payload part
        self.members = []      # ustar-encoded payload members
        self.own_libs = set()  # 'libfoo.so.M.m' shipped by this package
        self.needed = {}       # 'libfoo.so.M.m' -> first file needing it

    def walk(self):
        for dirpath, dirnames, filenames in os.walk(self.stage):
            dirnames.sort()
            rel_dir = os.path.relpath(dirpath, self.stage)
            if rel_dir != ".":
                absdir = os.path.join(self.prefix, rel_dir)
                if absdir not in self.std_dirs:
                    self.entries.append(rel_dir + "/")
            # os.walk does not descend into symlinked dirs; package them as symlinks.
            for fn in sorted(filenames + [d for d in dirnames
                                          if os.path.islink(os.path.join(dirpath, d))]):
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                self.add_file(os.path.join(dirpath, fn), rel)

    def add_file(self, path, rel):
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            target = os.readlink(path)
            self.entries += [rel, f"@symlink {target}"]
            self.members.append(pkgcommon.ustar_member(rel, mode=0o755, symlink=target))
            return
        if not stat.S_ISREG(st.st_mode):
            raise PkgError(f"{rel}: unsupported file type")
        if st.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise PkgError(f"{rel}: setuid/setgid files are not supported")
        with open(path, "rb") as f:
            data = f.read()
        mode = 0o755 if st.st_mode & 0o111 else 0o644
        base = os.path.basename(rel)
        elf = elfinfo.read(path)
        annot = None
        if elf is not None and elf.is_shared_lib:
            m = pkgcommon.LIB_RE.match(base)
            if not m:
                raise PkgError(f"{rel}: OpenBSD shared libraries must be named libfoo.so.MAJOR.MINOR")
            annot, mode = "@lib", 0o644
            self.own_libs.add(base)
        elif elf is not None and elf.is_executable:
            annot, mode = "@bin", 0o755
        elif base.startswith("lib") and base.endswith(".a"):
            annot, mode = "@static-lib", 0o644
        elif rel.startswith("man/"):
            annot = "@man"
        elif rel.startswith("info/") and ".info" in base:
            annot = "@info"
        if elf is not None:
            for lib in elf.needed:
                self.needed.setdefault(lib, rel)
        self.entries.append(f"{annot} {rel}" if annot else rel)
        self.entries += [f"@sha {sha_b64(data)}", f"@size {len(data)}", f"@ts {int(st.st_mtime)}"]
        self.members.append(pkgcommon.ustar_member(rel, data, mode=mode))

    def compute_wantlib(self):
        pkglibs = installed_libs(self.sysroot)
        wantlib, auto_depends = set(), {}
        for lib, user in sorted(self.needed.items()):
            if lib in self.own_libs:
                continue
            m = pkgcommon.LIB_RE.match(lib)
            if not m:
                raise PkgError(f"{user}: NEEDED '{lib}' is not an OpenBSD libfoo.so.MAJOR.MINOR")
            want = f"{m['stem']}.{m['major']}.{m['minor']}"
            if any(os.path.exists(os.path.join(self.sysroot, d, lib)) for d in SYSTEM_LIB_DIRS):
                wantlib.add(want)
            elif lib in pkglibs:
                wantlib.add(want)
                name, pkgpath = pkglibs[lib]
                if not pkgpath:
                    raise PkgError(f"{lib}: providing package {name} has no pkgpath")
                stem = pkgcommon.split_pkgname(name)[0]
                auto_depends[pkgpath] = f"{pkgpath}:{stem}-*:{name}"
            else:
                raise PkgError(f"{user}: needs {lib}, found neither in the sysroot nor in "
                               "packages fetched with pkg_fetch.py")
        return sorted(wantlib), auto_depends

    def description(self):
        if self.a.desc_file:
            with open(self.a.desc_file) as f:
                desc = f.read().rstrip("\n")
        else:
            desc = self.a.desc or self.a.comment
        text = f"{self.a.comment}\n{desc}\n"
        if self.a.maintainer:
            text += f"\nMaintainer: {self.a.maintainer}\n"
        if self.a.www:
            text += f"\nWWW: {self.a.www}\n"
        return text.encode()

    def build(self):
        pkgcommon.split_pkgname(self.a.name)
        if "\n" in self.a.comment:
            raise PkgError("--comment must be a single line")
        self.walk()
        if not self.members:
            raise PkgError(f"{self.stage}: nothing to package")
        wantlib, depends = self.compute_wantlib()
        for d in self.a.depend:
            parts = d.split(":")
            if len(parts) != 3:
                raise PkgError(f"--depend '{d}': expected pkgpath:pattern:default")
            depends[parts[0]] = d

        desc = self.description()
        head = [f"@name {self.a.name}"]
        if self.a.version is not None:
            head.append(f"@version {self.a.version}")
        head += [f"@comment pkgpath={self.a.pkgpath} ftp=yes", f"@arch {self.a.arch}",
                 "+DESC", f"@sha {sha_b64(desc)}", f"@size {len(desc)}"]
        head += [f"@depend {d}" for _, d in sorted(depends.items())]
        head += [f"@wantlib {w}" for w in wantlib]
        head.append(f"@cwd {self.prefix}")
        contents = ("\n".join(head + self.entries) + "\n").encode()

        now = int(time.time())
        meta = b"".join(pkgcommon.ustar_member(name, data, mode=0o444, mtime=now,
                                               owner=pkgcommon.META_OWNER, typebits=False)
                        for name, data in (("+CONTENTS", contents), ("+DESC", desc)))
        tar = meta + b"".join(self.members) + pkgcommon.USTAR_END
        out = gzip.compress(tar, compresslevel=9, mtime=0)
        if self.a.sign:
            keyname = os.path.basename(self.a.sign)
            if not keyname.endswith("-pkg.sec"):
                raise PkgError("signing key must be named <name>-pkg.sec (pkg_add only "
                               "trusts /etc/signify/*-pkg.pub)")
            out = signify.sign_gzip(signify.SecretKey.load(self.a.sign), keyname, out,
                                    f"verify with {keyname[:-4]}.pub")
        with open(self.a.output, "wb") as f:
            f.write(out)
        return contents.decode()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", help="staged files, relative to --prefix")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--sysroot", required=True, help="sysroot made by fetch-sysroot.sh")
    ap.add_argument("--name", required=True, help="package name, e.g. foo-1.0 or foo-1.0p0")
    ap.add_argument("--pkgpath", required=True, help="e.g. misc/foo")
    ap.add_argument("--comment", required=True, help="one-line summary")
    ap.add_argument("--desc-file")
    ap.add_argument("--desc")
    ap.add_argument("--maintainer")
    ap.add_argument("--www")
    ap.add_argument("--prefix", default="/usr/local")
    ap.add_argument("--arch", default="amd64")
    ap.add_argument("--version", type=int, help="@version (bump to force updates)")
    ap.add_argument("--depend", action="append", default=[],
                    help="extra pkgpath:pattern:default dependency (repeatable)")
    ap.add_argument("--sign", metavar="KEY-pkg.sec", help="sign with an unencrypted signify key")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the packing list")
    a = ap.parse_args(argv)
    try:
        contents = PackageBuilder(a).build()
    except (PkgError, ValueError, OSError, signify.SignifyError) as e:
        print(f"pkg_create.py: {e}", file=sys.stderr)
        return 1
    if a.verbose:
        sys.stdout.write(contents)
    print(f"created {a.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
