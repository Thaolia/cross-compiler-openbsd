# cross-pkg-openbsd

Cross-compile C/C++ software for **OpenBSD/amd64** on Linux with **upstream clang/lld**,
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
| C, C++ (libc++), shared libs: PIE, IBT, OpenBSD notes identical to native binaries | |
| Dependencies from official packages (jq, sdl2 + X11 from `xbase`) | |
| CMake build of an SDL2 program, packaged with automatic `@wantlib`/`@depend` | |
| Package signatures byte-compatible with `signify -z` (checked against real packages) | |
| **sdl2demo runs on a real OpenBSD -current machine** (2026-11-14, snapshot of the same day) | |
| **CI on an OpenBSD 8.0 VM**: `pkg_add` of both example packages (dependencies fetched by pkg_add), both run, `pkg_delete` (2026-11-15) | |
| Docker images `openbsd-cross:8.0` / `:current` built and published by CI | |

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

## Docker

```sh
docker build -f docker/Dockerfile --build-arg OPENBSD_FLAVOR=8.0 -t openbsd-cross:8.0 .
docker run --rm -v "$PWD:/work" openbsd-cross:8.0 tests/build-example.sh tests/sdl2demo 8.0 out
```

## Known limitations

- OpenBSD's clang has local patches (e.g. **retguard**) that upstream clang lacks; binaries
  work but miss that hardening.
- **-current breaks ABI** regularly (e.g. `libutil` 22 → 23 already differs from 8.0): rebuild
  the sysroot and packages often, never mix flavors.
- Static linking is untested and discouraged.
- Builds that run target binaries at build time need a native build (OpenBSD VM).

## Layout

`src/sysroot` sysroot fetch · `src/wrappers` compiler wrappers · `src/pkgtools` signify,
pkg_create, pkg_fetch · `src/cmake` `src/meson` `src/autotools` build-system glue ·
`src/keys` trusted OpenBSD keys · `docker/` image · `tests/` examples and unit tests.
