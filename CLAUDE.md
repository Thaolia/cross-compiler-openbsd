# CLAUDE.md — cross-pkg-openbsd

Linux → OpenBSD/amd64 cross toolchain (upstream clang/lld + official sets as sysroot) and
pure-Python OpenBSD package creation. Design: docs/ARCHITECTURE.md. Study: docs/FAISABILITE.md.

## Conventions
- Project docs in French (docs/, README.fr.md); README.md, code comments and commits in English.
- Shell scripts are POSIX `sh` with `set -eu`; Python is stdlib + `cryptography` only.
- Never add a dependency on OpenBSD-only tools: everything must run on a Linux host / Docker.
- Generated material (sysroots, caches, keys, outputs) lives in `build/` (gitignored). Never commit `*.sec`.

## Trust rules (always)
- Every downloaded set or package MUST be signify-verified against `src/keys/` before use.
- New OpenBSD keys go in `src/keys/` only after cross-checking two sources
  (GitHub `openbsd/src/etc/signify` and the signed `base` set's `etc/signify`).

## Compatibility rules
- Generated packages must match OpenBSD's format byte-for-byte where observable: compare any
  change against a real package (`tar tvzf`, header fields, `+CONTENTS` layout) before claiming it works.
- Binaries: compare `readelf -lW`/`-nW` with a native binary from the matching base set.
- Keep stable and -current strictly separate (sysroot, packages, outputs).

## Verification before declaring done
1. `python3 -m unittest discover -s tests`
2. `tests/build-example.sh tests/sdl2demo 8.0 build/out/8.0 build/keys/test-pkg.sec` (and `current`);
   same for `tests/nuitkademo` and `tests/nuitkastandalone` when touching `src/nuitka/`,
   `elfinfo.py`, `pkg_create.py` or `build-example.sh`
3. `openbsd-signify verify-gzip -p build/keys/test-pkg.pub <pkg>`
4. Real-OpenBSD checks (pkg_add, execution) only happen in CI `test-stable` or a QEMU VM — say so
   explicitly when they were not run.

## Local environment notes
- No sudo package installs: lld was extracted locally to `build/hosttools/` from the Debian .deb;
  export `OPENBSD_LLD=$PWD/build/hosttools/root/usr/lib/llvm-21/bin/ld.lld`.
- Install the front-end for dev with `src/wrappers/install.sh build 8.0 current`.
- Nuitka (pinned, see `NUITKA_VERSION` in `src/nuitka/nuitka_cross.py`) lives in `build/nuitka/lib`:
  `uv pip install --python /usr/bin/python3 --target build/nuitka/lib nuitka==<ver>`, then
  export `OPENBSD_NUITKA_PATH=$PWD/build/nuitka/lib`. The host Python must match the sysroot
  python package's major.minor (3.14).
- Nuitka integration relies on Nuitka internals (plugin patch, `--show-scons` output,
  `@link_input.txt`, `--report` XML, `<module>.so` names in `*.dist`): after bumping Nuitka, rerun
  both Nuitka examples and compare `readelf -lW` with the sysroot's `usr/local/bin/python3.X`.
- Standalone builds must not embed build-host paths: check with
  `strings -a <exe> | grep -E '/home/|/tmp/'` (host `_sysconfigdata_*` are excluded for that reason).
