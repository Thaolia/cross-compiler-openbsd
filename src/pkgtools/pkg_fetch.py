#!/usr/bin/env python3
"""Install official OpenBSD packages (and their dependencies) into a cross sysroot.

    pkg_fetch.py --sysroot build/sysroot/8.0 oniguruma curl

Every package signature is verified against the trusted keys in src/keys/.
Installed packages are registered in <sysroot>/var/db/pkg/<name>/+CONTENTS so that
pkg_create.py can turn needed libraries into @depend lines.
"""
import argparse
import os
import re
import subprocess
import sys
import tarfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import pkgcommon  # noqa: E402
import signify  # noqa: E402

SRC = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
KEYDIR = os.path.join(SRC, "keys")
DEFAULT_MIRROR = "https://cdn.openbsd.org/pub/OpenBSD"
LIB_DIRS = ("usr/lib", "usr/X11R6/lib", "usr/local/lib")
KEY_COMMENT_RE = re.compile(rb"^untrusted comment: verify with (openbsd-\d+-pkg)\.pub$")


class FetchError(Exception):
    pass


def sysroot_flavor(sysroot):
    info = os.path.join(sysroot, ".sysroot-info")
    try:
        with open(info) as f:
            fields = dict(l.rstrip("\n").split("=", 1) for l in f if "=" in l)
    except OSError:
        raise FetchError(f"{info} missing: create the sysroot with fetch-sysroot.sh first") from None
    return fields["flavor"]


def download(url, dest):
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)


def resolve_name(names, wanted, where):
    """Map a stem ('oniguruma'), a stem%branch ('python%3.14', as pkg_add accepts) or a full
    name ('oniguruma-6.9.10') to one package name of the index."""
    if wanted in names:
        return wanted
    want_stem, _, branch = wanted.partition("%")
    matches = []
    for n in names:
        try:
            stem, version, flavor = pkgcommon.split_pkgname(n)
        except ValueError:
            continue
        if stem == want_stem and (not branch or version == branch
                                  or version.startswith(branch + ".")):
            matches.append((flavor, n))
    plain = [n for fl, n in matches if not fl]
    if len(plain) == 1:
        return plain[0]
    if len(matches) == 1:
        return matches[0][1]
    if not matches:
        raise FetchError(f"no package named '{wanted}' in {where}")
    raise FetchError(f"'{wanted}' is ambiguous: {', '.join(n for _, n in matches)}")


class Fetcher:
    def __init__(self, sysroot, mirror):
        self.sysroot = os.path.abspath(sysroot)
        flavor = sysroot_flavor(self.sysroot)
        rel = "snapshots" if flavor == "current" else flavor
        self.url = f"{mirror}/{rel}/packages/amd64"
        self.cache = os.path.join(os.path.dirname(self.sysroot), "cache", flavor, "packages")
        os.makedirs(self.cache, exist_ok=True)
        self.dbdir = os.path.join(self.sysroot, "var/db/pkg")
        os.makedirs(self.dbdir, exist_ok=True)
        self._index = None

    def index(self):
        if self._index is None:
            path = os.path.join(self.cache, "index.txt")
            download(f"{self.url}/index.txt", path)
            with open(path) as f:
                self._index = [l.split()[-1][:-4] for l in f if l.rstrip().endswith(".tgz")]
        return self._index

    def resolve(self, wanted):
        return resolve_name(self.index(), wanted, self.url)

    def verified_package(self, name):
        path = os.path.join(self.cache, name + ".tgz")
        if not os.path.exists(path):
            print(f"==> downloading {name}.tgz", file=sys.stderr)
            download(f"{self.url}/{name}.tgz", path)
        with open(path, "rb") as f:
            data = f.read()
        _, _, comment = signify.split_gzip_header(data)
        m = KEY_COMMENT_RE.match((comment or b"").split(b"\n", 1)[0])
        if not m:
            raise FetchError(f"{name}: unsigned package or unexpected signer")
        keyfile = os.path.join(KEYDIR, m.group(1).decode() + ".pub")
        if not os.path.exists(keyfile):
            raise FetchError(f"{name}: signed by {m.group(1).decode()}, which is not in {KEYDIR}")
        try:
            signify.verify_gzip(signify.PublicKey.load(keyfile), data)
        except signify.SignifyError as e:
            os.unlink(path)
            raise FetchError(f"{name}: {e}") from None
        return data

    def check_wantlibs(self, name, plist):
        """Fail early, with the reason, when a library the package links against is absent from the sysroot."""
        missing = []
        for kw, arg in plist:
            if kw != "wantlib" or "/" in arg:
                continue
            stem, _, version = arg.rpartition(".")
            stem, _, major = stem.rpartition(".")
            if not stem or not major.isdigit():
                continue
            prefix = f"lib{stem}.so.{major}."
            if not any(os.path.isdir(d) and any(f.startswith(prefix) for f in os.listdir(d))
                       for d in (os.path.join(self.sysroot, s) for s in LIB_DIRS)):
                missing.append(arg)
        if missing:
            raise FetchError(f"{name} needs libraries missing from the sysroot: {', '.join(missing)} "
                             "(X11/GL libraries come from the xbase set: fetch-sysroot.sh <flavor> <dir> "
                             "base comp xbase)")

    def install(self, wanted, seen):
        name = self.resolve(wanted)
        if name in seen or os.path.isdir(os.path.join(self.dbdir, name)):
            return
        seen.add(name)
        data = self.verified_package(name)
        tar = pkgcommon.read_signed_tgz(data)
        first = tar.next()
        if first is None or first.name != "+CONTENTS":
            raise FetchError(f"{name}: first member is not +CONTENTS")
        contents = tar.extractfile(first).read().decode()
        plist = list(pkgcommon.parse_plist(contents))

        for kw, arg in plist:
            if kw == "depend":
                parts = arg.split(":")
                if len(parts) != 3:
                    raise FetchError(f"{name}: bad @depend '{arg}'")
                dep = parts[2]
                try:
                    self.resolve(dep)
                except FetchError:
                    dep = pkgcommon.split_pkgname(dep)[0]
                self.install(dep, seen)

        # Map each payload path to its destination using @cwd.
        dest_of = {}
        cwd = None
        for kw, arg in plist:
            if kw == "cwd":
                cwd = arg
            elif cwd is not None and (kw is None or kw in ("bin", "lib", "static-lib", "man",
                                                           "info", "file", "so", "shell")):
                path = arg
                if not path.endswith("/"):
                    dest_of[path] = os.path.join(cwd.lstrip("/"), path)

        print(f"==> installing {name} into sysroot", file=sys.stderr)
        for member in tar:
            if member.name.startswith("+"):
                continue
            rel = dest_of.get(member.name)
            if rel is None:
                # Not a plain payload file (e.g. @rcscript): irrelevant to a cross sysroot.
                continue
            target = pkgcommon.safe_member_path(self.sysroot, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.lexists(target):
                os.unlink(target)
            if member.issym():
                os.symlink(member.linkname, target)
            elif member.islnk():
                src = dest_of.get(member.linkname)
                if src is None:
                    raise FetchError(f"{name}: hard link to unknown {member.linkname}")
                os.link(pkgcommon.safe_member_path(self.sysroot, src), target)
            elif member.isfile():
                with tar.extractfile(member) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                os.chmod(target, 0o644 | (member.mode & 0o111))
            else:
                raise FetchError(f"{name}: unsupported member type for {member.name}")

        self.check_wantlibs(name, plist)
        regdir = os.path.join(self.dbdir, name)
        os.makedirs(regdir)
        with open(os.path.join(regdir, "+CONTENTS"), "w") as f:
            f.write(contents)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sysroot", required=True)
    ap.add_argument("--mirror", default=os.environ.get("OPENBSD_MIRROR", DEFAULT_MIRROR))
    ap.add_argument("packages", nargs="+")
    a = ap.parse_args(argv)
    try:
        fetcher = Fetcher(a.sysroot, a.mirror)
        seen = set()
        for p in a.packages:
            fetcher.install(p, seen)
        libdir = os.path.join(fetcher.sysroot, "usr/local/lib")
        if os.path.isdir(libdir):
            subprocess.run([os.path.join(SRC, "sysroot/link-versioned-libs.sh"), libdir], check=True)
    except (FetchError, OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as e:
        print(f"pkg_fetch.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
