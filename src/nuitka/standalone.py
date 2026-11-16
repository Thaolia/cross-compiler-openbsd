"""Standalone layout for nuitka_cross.py: turn Nuitka's host *.dist directory into an OpenBSD one.

The host build gives the list of extension modules the program needs (compilation report) and
its data files (*.dist). Every ELF file is then taken from the sysroot instead of the host:
extension modules from the python package's lib-dynload (or site-packages of py3-* packages
installed with pkg_fetch.py), and the package libraries they need (libpython, libintl, ...).
Libraries from the base sets (usr/lib, usr/X11R6/lib) are not copied: they come with the OS.

The program finds the copied libraries through its DT_RPATH $ORIGIN, which OpenBSD's ld.so also
uses for the dependencies of dlopen()ed extension modules (they have no rpath of their own).
"""
import os
import shutil
import xml.etree.ElementTree as ET

import elfinfo

BASE_LIB_DIRS = ("usr/lib", "usr/X11R6/lib")
PACKAGE_LIB_DIR = "usr/local/lib"
# Test and example modules of the stdlib: some stdlib code tries to import them, never needed.
SKIPPED_STDLIB_PREFIXES = ("_test", "_ctypes_test", "xx", "_xxtestfuzz")


class StandaloneError(Exception):
    pass


def parse_report(path):
    """From Nuitka's --report XML, return (extension modules {name: host source path},
    every module name the program uses or includes)."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        raise StandaloneError(f"cannot read Nuitka report {path}: {e}") from None
    if root.get("completion") != "yes":
        raise StandaloneError(f"Nuitka report {path} is incomplete")
    extensions, used = {}, set()
    for mod in root.iter("module"):
        name = mod.get("name")
        used.add(name)
        if mod.get("kind") == "PythonExtensionModule":
            extensions[name] = mod.get("source_path", "")
        for usage in mod.iter("module_usage"):
            if usage.get("finding") != "excluded":
                used.add(usage.get("name"))
    return extensions, used


def ext_suffixes(mm):
    return (f".cpython-{mm.replace('.', '')}.so", ".abi3.so", ".so")


def target_extension(sysroot, mm, name):
    """Path in the sysroot of extension module 'name' (dotted), or None."""
    parts = name.split(".")
    pylib = os.path.join(sysroot, f"usr/local/lib/python{mm}")
    bases = [os.path.join(pylib, "site-packages")]
    if len(parts) == 1:
        bases.insert(0, os.path.join(pylib, "lib-dynload"))
    for base in bases:
        for suffix in ext_suffixes(mm):
            path = os.path.join(base, *parts[:-1], parts[-1] + suffix)
            if os.path.isfile(path):
                return path
    return None


def stdlib_extensions(sysroot, mm):
    """Names of the extension modules shipped in the python package's lib-dynload."""
    d = os.path.join(sysroot, f"usr/local/lib/python{mm}/lib-dynload")
    suffix = ext_suffixes(mm)[0]
    return {f[:-len(suffix)] for f in os.listdir(d) if f.endswith(suffix)}


def is_host_stdlib(source_path):
    return source_path.startswith("${sys.prefix}/") or "/lib-dynload/" in source_path


def plan_extensions(sysroot, mm, host_exts, used, warn):
    """Return {dist relative path: sysroot path} of the extension modules to ship.

    host_exts: those Nuitka shipped for the host. used: every module name in the program; it
    also selects stdlib modules that are built into the host interpreter but are separate
    shared objects in OpenBSD's python package (math, zlib, _struct...)."""
    plan = {}
    for name, source in sorted(host_exts.items()):
        path = target_extension(sysroot, mm, name)
        if path is None:
            if is_host_stdlib(source):
                warn(f"extension module {name} is not in OpenBSD's python package: skipped")
                continue
            raise StandaloneError(
                f"no OpenBSD build of extension module {name} in the sysroot: install the "
                f"py3-* package providing it with openbsd-pkg-fetch")
        plan[name.replace(".", "/") + ".so"] = path
    for name in sorted(stdlib_extensions(sysroot, mm) & used):
        if name.startswith(SKIPPED_STDLIB_PREFIXES) or name in host_exts:
            continue
        plan[name + ".so"] = target_extension(sysroot, mm, name)
    return plan


def find_library(sysroot, lib):
    """('base' | 'package', path) for a NEEDED name, or None."""
    for d in BASE_LIB_DIRS:
        if os.path.isfile(os.path.join(sysroot, d, lib)):
            return "base", os.path.join(sysroot, d, lib)
    path = os.path.join(sysroot, PACKAGE_LIB_DIR, lib)
    if os.path.isfile(path):
        return "package", path
    return None


def bundle_libraries(sysroot, elf_paths):
    """Return {library name: sysroot path} of the package libraries needed, directly or not,
    by the given ELF files. Base libraries are left to the OS."""
    bundled, todo, seen = {}, list(elf_paths), set()
    while todo:
        path = todo.pop()
        info = elfinfo.read(path)
        if info is None:
            raise StandaloneError(f"{path} is not an ELF file")
        for lib in info.needed:
            if lib in seen:
                continue
            seen.add(lib)
            found = find_library(sysroot, lib)
            if found is None:
                raise StandaloneError(f"{os.path.basename(path)} needs {lib}, which is not in "
                                      "the sysroot")
            kind, libpath = found
            if kind == "package":
                bundled[lib] = libpath
                todo.append(libpath)
    return bundled


def host_data_files(host_dist, host_exe, ext_rel_paths, warn):
    """Non-ELF files of the host dist (data files Nuitka included), as relative paths. ELF
    files are host builds: the program, extension modules and libraries are replaced; any
    other one is dropped with a warning."""
    data = []
    for dirpath, dirnames, filenames in os.walk(host_dist):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, host_dist)
            if elfinfo.read(full) is None:
                data.append(rel)
            elif full != host_exe and rel not in ext_rel_paths and not fn.startswith("lib"):
                warn(f"dropping host ELF file {rel} (no OpenBSD equivalent known)")
    return data


def copy_file(src, dst, mode):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)
