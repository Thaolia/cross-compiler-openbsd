# cross-pkg-openbsd

Cross-compile C/C++ software (and Python programs, through **Nuitka**) for **OpenBSD/amd64**
on Linux with **upstream clang/lld**,
for the latest release (8.0) and **-current**, and produce **signed `.tgz` packages that
OpenBSD's `pkg_add` installs directly**. Built to run in Docker / GitHub Actions.

No compiler is built: clang and lld are already cross compilers. The toolchain is
a sysroot extracted from the **official, signify-verified install sets**, plus thin
wrappers, CMake/Meson/autotools glue, and a pure-Python package creator.

French version: [README.fr.md](README.fr.md) · Feasibility study: [docs/FAISABILITE.md](docs/FAISABILITE.md) ·
Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Status

| Verified | Not yet verified |
|---|---|
| Sysroot fetch + signify verification (8.0 and -current) | Installing packages on a real -current machine with `pkg_add` |
| | Running a Nuitka-built program on OpenBSD, accelerated and standalone (CI job `test-stable` added, not run yet) |
| C, C++ (libc++), shared libs: PIE, IBT, OpenBSD notes identical to native binaries | |
| Dependencies from official packages (jq, sdl2 + X11 from `xbase`) | |
| CMake build of an SDL2 program, packaged with automatic `@wantlib`/`@depend` | |
| Package signatures byte-compatible with `signify -z` (checked against real packages) | |
| **sdl2demo runs on a real OpenBSD -current machine** (2026-11-14, snapshot of the same day) | |
| **CI on an OpenBSD 8.0 VM**: `pkg_add` of both example packages (dependencies fetched by pkg_add), both run, `pkg_delete` (2026-11-15) | |
| Docker images `openbsd-cross:8.0` / `:current` built and published by CI | |
| Nuitka (accelerated mode) program cross-built for 8.0 and -current: ELF marks identical to the official `python3.14`, packaged with the same `@depend` as official `py3-*` packages | |
| Nuitka standalone program cross-built for 8.0 and -current: libpython, extension modules and package libraries copied from the sysroot, `DT_RPATH $ORIGIN`, no `@depend`, only base-system `@wantlib` | |

## Requirements

clang + lld (tested with LLVM 21), python3 with `cryptography`, curl, tar, cmake/meson as needed.

## Quick start

```sh
src/wrappers/install.sh /opt/openbsd-cross 8.0 current
export PATH=/opt/openbsd-cross/bin:$PATH
openbsd-fetch-sysroot 8.0 /opt/openbsd-cross/sysroot/8.0 base comp xbase
openbsd-fetch-sysroot current /opt/openbsd-cross/sysroot/current base comp xbase

x86_64-unknown-openbsd8.0-cc -O2 -o hello hello.c        # PIE, IBT, like OpenBSD's cc

# dependencies: official packages, signature-checked, installed into the sysroot
openbsd-pkg-fetch --sysroot "$(x86_64-unknown-openbsd8.0-sysroot)" sdl2

# full example: CMake build + package
openbsd-signify generate -p mykey-pkg.pub -s mykey-pkg.sec
tests/build-example.sh tests/sdl2demo 8.0 out mykey-pkg.sec
```

On OpenBSD: copy `mykey-pkg.pub` to `/etc/signify/`, then `pkg_add ./sdl2demo-0.1.tgz`
(or `pkg_add -D unsigned` for unsigned packages).

Build systems:
- CMake: `-DCMAKE_TOOLCHAIN_FILE=<prefix>/share/openbsd-cross/openbsd-toolchain.cmake -DOPENBSD_FLAVOR=8.0`
- Meson: `--cross-file <prefix>/share/openbsd-cross/openbsd-8.0.ini`
- autotools: `CONFIG_SITE=<prefix>/share/openbsd-cross/config.site ./configure --host=x86_64-unknown-openbsd8.0`

## Python programs (Nuitka)

`openbsd-nuitka` compiles a Python program with [Nuitka](https://nuitka.net) for OpenBSD, in
one of two modes:

- **accelerated** (default): one executable that links to `libpython` and uses the standard
  library of the official `python` package, which the OpenBSD package depends on.
- **standalone** (`--mode standalone`): a directory with the executable, the extension modules
  and the package libraries it needs (`libpython`, `libintl`, `libsqlite3`...), all copied from the
  official packages in the sysroot. It runs without the `python` package; base-system libraries
  (`libc`, `libssl`...) come with the OS, so the build only runs on the matching release.

```sh
openbsd-pkg-fetch --sysroot "$(x86_64-unknown-openbsd8.0-sysroot)" python%3.14
openbsd-nuitka --flavor 8.0 -o stage/usr/local/bin/hello hello.py   # [-- <nuitka options>]
openbsd-nuitka --flavor 8.0 --print-depend   # --depend argument for openbsd-pkg-create
openbsd-nuitka --flavor 8.0 --mode standalone -o stage/usr/local/libexec/hello hello.py
ln -s ../libexec/hello/hello stage/usr/local/bin/hello
tests/build-example.sh tests/nuitkademo 8.0 out mykey-pkg.sec        # full examples
tests/build-example.sh tests/nuitkastandalone 8.0 out mykey-pkg.sec
```

- The Python running Nuitka must have **the target's major.minor** (3.14 for 8.0 and -current
  today): set `OPENBSD_NUITKA_PYTHON` (e.g. a `uv python install 3.14` interpreter) and
  `OPENBSD_NUITKA_PATH` if Nuitka is installed with `pip install --target`. Nuitka 4.3.5 is the
  version checked; it also needs a host C compiler (`gcc`).
- Nuitka cannot cross-compile, so it first builds for the host; the generated C is then rebuilt
  with the OpenBSD compiler against the sysroot's Python headers. Local modules are compiled in
  (`--follow-imports`). Accelerated: the standard library comes from the target's python package.
  Standalone: it is embedded as bytecode of the **host** Python's standard library (use a vanilla
  CPython such as uv's rather than a distribution-patched one), except `_sysconfigdata`, taken from
  the sysroot.
- Third-party C extension modules are not compiled: accelerated programs depend on the
  official `py3-*` package (`openbsd-pkg-create --depend ...`); standalone builds copy them from
  the sysroot, so fetch that package first (`openbsd-pkg-fetch ... py3-foo`).
- Standalone packages: shared objects outside `lib/` are packaged as `@so` (like the extension
  modules of the official python package), so `pkg_add` does not register their directory with
  `ldconfig`.

## Docker

```sh
docker build -f docker/Dockerfile --build-arg OPENBSD_FLAVOR=8.0 -t openbsd-cross:8.0 .
docker run --rm -v "$PWD:/work" openbsd-cross:8.0 tests/build-example.sh tests/sdl2demo 8.0 out
```

The image also contains the python package in its sysroot, a uv-managed CPython of the same
version and Nuitka (build args `OPENBSD_PYTHON`, default 3.14, and `NUITKA_VERSION`, default 4.3.5).

## Known limitations

- OpenBSD's clang has local patches (e.g. **retguard**) that upstream clang lacks; binaries
  work but miss that hardening.
- **-current breaks ABI** regularly (e.g. `libutil` 22 → 23 already differs from 8.0): rebuild
  the sysroot and packages often, never mix flavors.
- Static linking is untested and discouraged.
- Builds that run target binaries at build time need a native build (OpenBSD VM).
- Nuitka: the C code is generated by the host's patch release (e.g. 3.14.7) and compiled
  against the target's headers (3.14.9 on -current today). CPython keeps its ABI within a minor
  release, but prefer the same patch level and rebuild after a python update. In standalone
  builds, the embedded standard library is also the host's patch release.
- Nuitka `--onefile` is not supported (standalone directory only).

## Layout

`src/sysroot` sysroot fetch · `src/wrappers` compiler wrappers · `src/pkgtools` signify,
pkg_create, pkg_fetch · `src/nuitka` Nuitka cross build · `src/cmake` `src/meson` `src/autotools` build-system glue ·
`src/keys` trusted OpenBSD keys · `docker/` image · `tests/` examples and unit tests.
