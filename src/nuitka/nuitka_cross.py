#!/usr/bin/env python3
"""Compile a Python program with Nuitka into an OpenBSD/amd64 executable.

    nuitka_cross.py --flavor 8.0 -o stage/usr/local/bin/hello hello.py [-- <nuitka options>]
    nuitka_cross.py --flavor 8.0 --mode standalone -o stage/usr/local/libexec/hello hello.py
    nuitka_cross.py --flavor 8.0 --mode onefile -o hello hello.py

The official python package must be installed in the sysroot first (openbsd-pkg-fetch
--sysroot <sysroot> python%3.14). The Python running Nuitka must have the target's major.minor
version.

accelerated (default): -o is the executable; it links to libpython and uses the stdlib of the
python package, which the OpenBSD package must depend on (--print-depend gives the matching
pkg_create.py --depend argument).
standalone: -o is a directory holding the executable (named after the script, or --name), the
extension modules and the package libraries it needs, taken from the sysroot; it runs without
the python package. The stdlib is embedded as bytecode compiled by the host Python.
onefile: -o is a single executable holding the standalone directory, which it extracts to
$TMPDIR (or /tmp) and runs from there on each start.

Nuitka cannot cross-compile, so it first runs a normal build for the host: that produces every
generated file (C code, constants blob, build_definitions.h, list of objects). The same C files
are then compiled again with the OpenBSD cross compiler against the sysroot's Python headers,
and linked against the sysroot's libpython. A user plugin (openbsd_target_plugin.py) makes the
embedded sys.executable/sys.prefix those of the OpenBSD interpreter.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pkgtools"))
import elfinfo  # noqa: E402
import onefile  # noqa: E402
import pkgcommon  # noqa: E402
import standalone  # noqa: E402

PLUGIN = os.path.join(HERE, "openbsd_target_plugin.py")
NUITKA_VERSION = "4.3.5"  # version the plugin and the build replay were checked against
TARGET_PREFIX = "/usr/local"

# Nuitka only adapts the Python headers (and defines this) on Linux and Windows hosts; a native
# OpenBSD build keeps the stock headers, so the cross build does the same.
HOST_ONLY_DEFINES = {"_NUITKA_ADAPTED_PYTHON_HEADERS"}
# Written to build_definitions.h when the host Python is not installed in a system location
# (uv, python-build-standalone): it would make the program use the host prefix as PYTHONHOME.
# The official OpenBSD python finds its prefix itself; so does the program without it.
HOST_ONLY_BUILD_DEFINITIONS = {"PYTHON_HOME_PATH"}
PY_INCLUDE_RE = re.compile(r"^(.*/include/python3\.\d+[a-z]*)(/.*)?$")

# The program embeds the interpreter and loads the same extension modules as the official
# python binary, so it gets the same OpenBSD exemptions (W^X, indirect branch tracking).
INTERPRETER_MARKS = ((elfinfo.PT_OPENBSD_WXNEEDED, "-Wl,-z,wxneeded"),
                     (elfinfo.PT_OPENBSD_NOBTCFI, "-Wl,-z,nobtcfi"))

HOST_PROBE = """\
import json, os, sys, sysconfig
import nuitka
from nuitka.Version import getNuitkaVersion
print(json.dumps({"version": "%d.%d" % sys.version_info[:2],
                  "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
                  "nuitka_dir": os.path.dirname(os.path.abspath(nuitka.__file__)),
                  "nuitka_version": getNuitkaVersion(),
                  "sysconfigdata": sorted(
                      f[:-3] for f in os.listdir(sysconfig.get_path("stdlib"))
                      if f.startswith("_sysconfigdata_") and f.endswith(".py"))}))
"""


class CrossError(Exception):
    pass


def log(msg):
    print(f"openbsd-nuitka: {msg}", file=sys.stderr)


def target_python(sysroot):
    """Return (major.minor, full version, include dir) of the Python installed in the sysroot."""
    incs = sorted(glob.glob(os.path.join(sysroot, "usr/local/include/python3.*")))
    if not incs:
        raise CrossError(f"no Python headers in {sysroot}/usr/local/include: run "
                         f"'openbsd-pkg-fetch --sysroot {sysroot} python%3.X' first")
    if len(incs) > 1:
        raise CrossError(f"several Pythons in the sysroot: {', '.join(incs)}")
    with open(os.path.join(incs[0], "patchlevel.h")) as f:
        full = parse_patchlevel(f.read())
    mm = ".".join(full.split(".")[:2])
    if os.path.basename(incs[0]) != f"python{mm}":
        raise CrossError(f"{incs[0]} does not match its PY_VERSION {full}")
    with open(os.path.join(incs[0], "pyconfig.h")) as f:
        if gil_disabled(f.read()):
            raise CrossError("the sysroot Python is a free-threaded build, which is not supported")
    return mm, full, incs[0]


def parse_patchlevel(text):
    m = re.search(r'^#define\s+PY_VERSION\s+"(\d+\.\d+\.\d+)[^"]*"', text, re.M)
    if not m:
        raise CrossError("PY_VERSION not found in patchlevel.h")
    return m.group(1)


def gil_disabled(pyconfig_text):
    return re.search(r"^#define\s+Py_GIL_DISABLED\s+1\b", pyconfig_text, re.M) is not None


def python_depend(sysroot, mm):
    """@depend argument for the python package registered in the sysroot, written like the
    official py3-* packages do ('python->=3.14', which python-2.7 does not satisfy)."""
    dbdir = os.path.join(sysroot, "var/db/pkg")
    pkgs = [p for p in (os.listdir(dbdir) if os.path.isdir(dbdir) else [])
            if p.startswith(f"python-{mm}.")]
    if len(pkgs) != 1:
        raise CrossError(f"python-{mm} is not registered in {dbdir}: install it with "
                         f"'openbsd-pkg-fetch --sysroot {sysroot} python%{mm}'")
    if not glob.glob(os.path.join(sysroot, f"usr/local/lib/libpython{mm}.so.*")):
        raise CrossError(f"libpython{mm}.so.* missing from {sysroot}/usr/local/lib")
    with open(os.path.join(dbdir, pkgs[0], "+CONTENTS")) as f:
        return depend_from_plist(f.read(), mm)


def depend_from_plist(contents, mm):
    plist = list(pkgcommon.parse_plist(contents))
    name = next((arg for kw, arg in plist if kw == "name"), None)
    pkgpath = next((arg.split()[0].split("=", 1)[1] for kw, arg in plist
                    if kw == "comment" and arg.startswith("pkgpath=")), None)
    if not name or not pkgpath:
        raise CrossError("python's +CONTENTS has no @name or pkgpath")
    return f"{pkgpath}:python->={mm}:{name}"


def interpreter_link_flags(phtypes):
    return [flag for pt, flag in INTERPRETER_MARKS if pt in phtypes]


MODES = ("accelerated", "standalone", "onefile")
# Options that change what Nuitka builds or where: the replay below relies on the ones it sets.
FORBIDDEN_OPTIONS = ("--mode", "--standalone", "--onefile", "--module", "--lto",
                     "--static-libpython", "--file-reference-choice", "--output-dir",
                     "--output-filename", "--report", "--remove-output")


def check_extra_options(extra):
    for opt in extra:
        if opt.split("=", 1)[0] in FORBIDDEN_OPTIONS:
            raise CrossError(f"Nuitka option {opt} is not supported (set by openbsd-nuitka)")


def nuitka_command(python, script, outdir, extra, mode="accelerated"):
    # runtime file references: __file__ follows the installed binary, not the build tree.
    # The report lists the extension modules a standalone program needs.
    return [python, "-m", "nuitka", f"--mode={mode}", "--follow-imports",
            "--file-reference-choice=runtime", "--lto=no", "--static-libpython=no",
            "--show-scons", f"--report={os.path.join(outdir, 'report.xml')}",
            f"--user-plugin={PLUGIN}", f"--output-dir={outdir}", *extra, script]


def parse_compile_line(lines, nuitka_dir, target_include):
    """From Nuitka's --show-scons output, take the first C compile command and return the
    flags to reuse for the target: -std, -D (minus host-only ones), and -I with the host Python
    include dirs mapped onto the sysroot's."""
    for line in lines:
        if " -c " not in line or "-D_NUITKA_" not in line:
            continue
        try:
            argv = shlex.split(line)
        except ValueError:
            continue
        if not argv or not argv[-1].endswith(".c"):
            continue
        flags = []
        for a in argv:
            if a.startswith("-std="):
                flags.append(a)
            elif a.startswith("-D"):
                if a[2:].split("=", 1)[0] not in HOST_ONLY_DEFINES:
                    flags.append(a)
            elif a.startswith("-I"):
                inc = a[2:]
                m = PY_INCLUDE_RE.match(inc)
                if inc == "." or os.path.commonpath([nuitka_dir, os.path.abspath(inc)]) == nuitka_dir:
                    flags.append(a)
                elif m:
                    mapped = target_include + (m.group(2) or "")
                    if os.path.isdir(mapped):
                        flags.append("-I" + mapped)
                # anything else (Nuitka's adapted-headers cache) is host-only
        return flags
    raise CrossError("no C compile command found in Nuitka's output (--show-scons)")


def drop_host_definitions(source_dir):
    """Remove the host-only macros from Nuitka's build_definitions.h."""
    path = os.path.join(source_dir, "build_definitions.h")
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        raise CrossError(f"{path} missing: did the host build run?") from None
    kept = [l for l in lines
            if not (l.startswith("#define ") and l.split()[1] in HOST_ONLY_BUILD_DEFINITIONS)]
    if len(kept) != len(lines):
        with open(path, "w") as f:
            f.writelines(kept)


def link_sources(source_dir):
    """C sources of the objects scons linked, from its @link_input.txt."""
    path = os.path.join(source_dir, "@link_input.txt")
    try:
        with open(path) as f:
            objs = [shlex.split(l)[0] for l in f if l.strip()]
    except OSError:
        raise CrossError(f"{path} missing: did the host build run?") from None
    srcs = []
    for o in objs:
        if not o.endswith(".o"):
            raise CrossError(f"unexpected link input '{o}' in {path}")
        srcs.append(o[:-2] + ".c")
    return srcs


def run_streamed(cmd, env, verbose):
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace")
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip("\n"))
        if verbose:
            print(line, end="", file=sys.stderr)
    if proc.wait() != 0:
        if not verbose:
            print("\n".join(lines[-40:]), file=sys.stderr)
        raise CrossError(f"{cmd[0]} {' '.join(cmd[1:3])} failed (exit {proc.returncode})")
    return lines


def compile_all(cc, flags, source_dir, objdir, sources, jobs, verbose):
    def one(src):
        obj = os.path.join(objdir, src[:-2] + ".o")
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        cmd = [cc, "-c", "-O3", "-fwrapv", "-fvisibility=hidden", "-Wno-c23-extensions",
               *flags, "-o", obj, src]
        if verbose:
            log(" ".join(shlex.quote(c) for c in cmd))
        r = subprocess.run(cmd, cwd=source_dir, capture_output=True, text=True)
        if r.returncode != 0:
            raise CrossError(f"compiling {src} failed:\n{r.stderr}")
        return obj

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        return list(ex.map(one, sources))


def target_sysconfig_modules(sysroot, mm, dest):
    """Copy the sysroot python's _sysconfigdata module(s) into dest and return their names:
    a standalone program embeds the stdlib of the host Python, but sysconfig.get_config_vars()
    on OpenBSD imports _sysconfigdata__openbsdN_."""
    names = []
    for path in sorted(glob.glob(os.path.join(sysroot, f"usr/local/lib/python{mm}",
                                              "_sysconfigdata_*.py"))):
        os.makedirs(dest, exist_ok=True)
        shutil.copyfile(path, os.path.join(dest, os.path.basename(path)))
        names.append(os.path.basename(path)[:-3])
    if not names:
        raise CrossError(f"no _sysconfigdata_*.py in the sysroot's python{mm} library")
    return names


def link(cc, out, objs, flags, sysroot, mm, verbose):
    cmd = [cc, "-o", out, *objs, *flags, f"-L{sysroot}/usr/local/lib", f"-lpython{mm}", "-lm"]
    if verbose:
        log(" ".join(shlex.quote(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise CrossError(f"link failed:\n{r.stderr}")


def assemble_standalone(host_dir, host_exe_name, out_dir, exe, sysroot, mm, verbose):
    """Fill out_dir (which holds the OpenBSD executable exe) like Nuitka's host *.dist, with
    the ELF files taken from the sysroot."""
    host_dists = glob.glob(os.path.join(host_dir, "*.dist"))
    if len(host_dists) != 1:
        raise CrossError(f"expected one *.dist directory in {host_dir}, got {host_dists}")
    host_dist = host_dists[0]
    host_exts, used = standalone.parse_report(os.path.join(host_dir, "report.xml"))
    warn = lambda msg: log(f"warning: {msg}")  # noqa: E731
    plan = standalone.plan_extensions(sysroot, mm, host_exts, used, warn)
    for rel, src in plan.items():
        standalone.copy_file(src, os.path.join(out_dir, rel), 0o755)
    libs = standalone.bundle_libraries(sysroot, [exe, *plan.values()])
    for name, src in sorted(libs.items()):
        standalone.copy_file(src, os.path.join(out_dir, name), 0o644)
    host_ext_paths = {n.replace(".", "/") + ".so" for n in host_exts}
    data = standalone.host_data_files(host_dist, os.path.join(host_dist, host_exe_name),
                                      host_ext_paths, warn)
    for rel in data:
        dst = os.path.join(out_dir, rel)
        if os.path.exists(dst):
            raise CrossError(f"data file {rel} clashes with a shipped binary")
        src = os.path.join(host_dist, rel)
        standalone.copy_file(src, dst, 0o755 if os.access(src, os.X_OK) else 0o644)
    log(f"standalone: {len(plan)} extension modules, {len(libs)} libraries "
        f"({', '.join(sorted(libs))}), {len(data)} data files")
    if verbose:
        for rel in sorted(plan):
            log(f"  {rel} <- {plan[rel]}")


def build_bootstrap(python, env, host_dir, lines, nuitka_dir, dist, exe, out, cc, strip, a):
    """Pack dist into the host build's onefile bootstrap and cross-compile it to out."""
    builds = glob.glob(os.path.join(host_dir, "*.onefile-build"))
    if len(builds) != 1:
        raise CrossError(f"expected one *.onefile-build directory in {host_dir}, got {builds}")
    source_dir = builds[0]
    compiles, sources = onefile.bootstrap_compile_lines(lines)
    flags = parse_compile_line(compiles, nuitka_dir, "/nonexistent")
    size = onefile.pack_payload(python, env, source_dir, dist, exe, onefile.temp_mode(flags),
                                a.jobs)
    log(f"onefile: payload of {size} bytes, bootstrap from {len(sources)} C files")
    objs = compile_all(cc, flags, source_dir, os.path.join(host_dir, "openbsd-onefile-obj"),
                       sources, a.jobs, a.verbose)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [cc, "-o", out, *objs]
    if a.verbose:
        log(" ".join(shlex.quote(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise CrossError(f"bootstrap link failed:\n{r.stderr}")
    if not a.no_strip:
        subprocess.run([strip, out], check=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("script", nargs="?", help="main Python file of the program")
    ap.add_argument("-o", "--output",
                    help="executable to write (accelerated, onefile) or directory to create "
                         "(standalone)")
    ap.add_argument("--mode", choices=MODES, default="accelerated")
    ap.add_argument("--name", help="standalone executable name, also the program's name "
                                   "inside a onefile one (default: script name)")
    ap.add_argument("--print-depend", action="store_true",
                    help="only print the pkg_create --depend argument for the python package "
                         "(accelerated programs)")
    ap.add_argument("--flavor", required=True, help="toolchain flavor, e.g. 8.0 or current")
    ap.add_argument("--python", default=os.environ.get("OPENBSD_NUITKA_PYTHON", "python3"),
                    help="interpreter that runs Nuitka (default: $OPENBSD_NUITKA_PYTHON or python3)")
    ap.add_argument("--nuitka-path", default=os.environ.get("OPENBSD_NUITKA_PATH"),
                    help="directory to prepend to PYTHONPATH to find Nuitka "
                         "(default: $OPENBSD_NUITKA_PATH)")
    ap.add_argument("--keep-build", metavar="DIR", help="build in DIR and keep it")
    ap.add_argument("--no-strip", action="store_true")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.print_depend and a.mode != "accelerated":
        ap.error("--print-depend only applies to accelerated programs")
    if not a.print_depend and (not a.script or not a.output):
        ap.error("script and -o/--output are required")
    if a.name and a.mode == "accelerated":
        ap.error("--name only applies to standalone and onefile programs")

    t = f"x86_64-unknown-openbsd{a.flavor}"
    try:
        check_extra_options(extra)
        cc = shutil.which(f"{t}-cc")
        strip = shutil.which(f"{t}-strip")
        if not cc or not strip:
            raise CrossError(f"{t}-cc/{t}-strip not in PATH (install the front-end)")
        sysroot = subprocess.run([f"{t}-sysroot"], capture_output=True, text=True,
                                 check=True).stdout.strip()
        mm, full, target_include = target_python(sysroot)
        depend = python_depend(sysroot, mm)
        if a.print_depend:
            print(depend)
            return 0
        target_exe = f"{TARGET_PREFIX}/bin/python{mm}"
        interp = elfinfo.read(os.path.join(sysroot, target_exe.lstrip("/")))
        if interp is None or not interp.is_executable:
            raise CrossError(f"{target_exe} in the sysroot is not an executable")
        out = os.path.abspath(a.output)
        bundled = a.mode in ("standalone", "onefile")
        if bundled:
            name = a.name or os.path.splitext(os.path.basename(a.script))[0]
            if not name or "/" in name:
                raise CrossError(f"bad executable name '{name}'")
        if a.mode == "standalone":
            if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
                raise CrossError(f"{out} exists and is not an empty directory")
        elif os.path.isdir(out):
            raise CrossError(f"{out} is a directory")

        env = dict(os.environ)
        if a.nuitka_path:
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in (os.path.abspath(a.nuitka_path), env.get("PYTHONPATH")) if p)
        # The host build must use the host compiler, whatever CC says for the target.
        for v in ("CC", "CXX", "CFLAGS", "CPPFLAGS", "LDFLAGS"):
            env.pop(v, None)
        r = subprocess.run([a.python, "-c", HOST_PROBE], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise CrossError(f"cannot import Nuitka with {a.python}:\n{r.stderr.strip()}")
        host = json.loads(r.stdout)
        if host["version"] != mm:
            raise CrossError(f"{a.python} is Python {host['version']} but the sysroot has "
                             f"Python {full}: Nuitka needs the same major.minor")
        if host["gil_disabled"]:
            raise CrossError(f"{a.python} is a free-threaded build; use a regular one")
        if not host["nuitka_version"].startswith(NUITKA_VERSION):
            log(f"warning: Nuitka {host['nuitka_version']}, only {NUITKA_VERSION} was checked")
        env["OPENBSD_NUITKA_EXECUTABLE"] = target_exe
        env["OPENBSD_NUITKA_PREFIX"] = TARGET_PREFIX

        work = os.path.abspath(a.keep_build) if a.keep_build else tempfile.mkdtemp()
        os.makedirs(work, exist_ok=True)
        try:
            host_dir = os.path.join(work, "host")
            nuitka_extra = list(extra)
            if bundled:
                mods = os.path.join(work, "target-modules")
                nuitka_extra += [f"--include-module={n}"
                                 for n in target_sysconfig_modules(sysroot, mm, mods)]
                # The host's describes Linux and holds build-host paths.
                nuitka_extra += [f"--nofollow-import-to={n}" for n in host["sysconfigdata"]]
                env["PYTHONPATH"] = os.pathsep.join(p for p in (env.get("PYTHONPATH"), mods) if p)
            log(f"Nuitka {host['nuitka_version']} host build ({a.mode}) of {a.script} "
                f"(Python {full} target)")
            lines = run_streamed(nuitka_command(a.python, os.path.abspath(a.script), host_dir,
                                                nuitka_extra, a.mode),
                                 env, a.verbose)
            builds = glob.glob(os.path.join(host_dir, "*.build"))
            if len(builds) != 1:
                raise CrossError(f"expected one *.build directory in {host_dir}, got {builds}")
            source_dir = builds[0]
            flags = parse_compile_line(lines, host["nuitka_dir"], target_include)
            sources = link_sources(source_dir)
            drop_host_definitions(source_dir)
            log(f"cross-compiling {len(sources)} C files for OpenBSD {a.flavor}")
            objs = compile_all(cc, flags, source_dir, os.path.join(work, "openbsd-obj"),
                               sources, a.jobs, a.verbose)
            link_flags = interpreter_link_flags(interp.phtypes)
            if bundled:
                # DT_RPATH, not DT_RUNPATH: ld.so also searches the program's DT_RPATH for the
                # dependencies of dlopen()ed extension modules.
                link_flags += ["-Wl,-rpath,$ORIGIN", "-Wl,--disable-new-dtags"]
                dist = out if a.mode == "standalone" else os.path.join(work, "openbsd-dist")
                exe = os.path.join(dist, name)
            else:
                exe = out
            if a.mode == "onefile":
                # The program watches its bootstrap from a thread; pthread is not in libc here.
                link_flags.append("-lpthread")
            os.makedirs(os.path.dirname(exe), exist_ok=True)
            link(cc, exe, objs, link_flags, sysroot, mm, a.verbose)
            if not a.no_strip:
                subprocess.run([strip, exe], check=True)
            if bundled:
                host_exe_name = os.path.basename(source_dir)[:-len(".build")] + ".bin"
                assemble_standalone(host_dir, host_exe_name, dist, exe, sysroot, mm, a.verbose)
            if a.mode == "onefile":
                build_bootstrap(a.python, env, host_dir, lines, host["nuitka_dir"], dist, exe,
                                out, cc, strip, a)
            log(f"wrote {out}")
        finally:
            if not a.keep_build:
                shutil.rmtree(work, ignore_errors=True)
    except (CrossError, standalone.StandaloneError, onefile.OnefileError) as e:
        log(f"error: {e}")
        return 1
    except subprocess.CalledProcessError as e:
        log(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
