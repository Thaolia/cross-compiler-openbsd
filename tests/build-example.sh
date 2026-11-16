#!/bin/sh
# End-to-end example: fetch dependencies, cross-build, create an OpenBSD package.
# usage: build-example.sh <example-dir> <flavor> <outdir> [signing-key-pkg.sec]
# <example-dir>/pkg.conf defines PKGNAME PKGPATH COMMENT DESC DEPS (official packages), and
# BUILD: cmake (default) or nuitka (then NUITKA_MAIN = main .py file, NUITKA_MODE = accelerated
# (default; installed as bin/<name>) or standalone (installed in libexec/<name>/, linked from
# bin/<name>), NUITKA_NAME = <name> (default: NUITKA_MAIN without .py)).
# Needs the front-end on PATH (docker image, or `src/wrappers/install.sh <prefix> <flavor>`);
# for nuitka, also OPENBSD_NUITKA_PYTHON / OPENBSD_NUITKA_PATH if Nuitka is not in python3.
set -eu
[ $# -ge 3 ] || { echo "usage: $0 <example-dir> <flavor> <outdir> [key.sec]" >&2; exit 2; }
src=$(cd "$1" && pwd)
flavor=$2
out=$(mkdir -p "$3" && cd "$3" && pwd)
key=${4:-}
PKGNAME= PKGPATH= COMMENT= DESC= DEPS= BUILD=cmake NUITKA_MAIN= NUITKA_MODE=accelerated
NUITKA_NAME=
. "$src/pkg.conf"
t=x86_64-unknown-openbsd$flavor
sysroot=$($t-sysroot)
bindir=$(dirname "$(command -v "$t-cc")")
work=$(mktemp -d)
extra_depend=
trap 'rm -rf "$work"' EXIT

if [ -n "$DEPS" ]; then
    # shellcheck disable=SC2086
    openbsd-pkg-fetch --sysroot "$sysroot" $DEPS
fi
case $BUILD in
    cmake)
        cmake -S "$src" -B "$work/build" -G Ninja \
            -DCMAKE_TOOLCHAIN_FILE="$bindir/../share/openbsd-cross/openbsd-toolchain.cmake" \
            -DOPENBSD_FLAVOR="$flavor" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local
        cmake --build "$work/build"
        DESTDIR="$work/stage" cmake --install "$work/build" --strip
        ;;
    nuitka)
        [ -n "$NUITKA_MAIN" ] || { echo "$0: BUILD=nuitka needs NUITKA_MAIN" >&2; exit 2; }
        name=${NUITKA_NAME:-$(basename "$NUITKA_MAIN" .py)}
        case $NUITKA_MODE in
            accelerated)
                openbsd-nuitka --flavor "$flavor" -o "$work/stage/usr/local/bin/$name" \
                    "$src/$NUITKA_MAIN"
                # Replaces the generic python-* dependency pkg_create would infer from libpython.
                extra_depend=$(openbsd-nuitka --flavor "$flavor" --print-depend)
                ;;
            standalone)
                openbsd-nuitka --flavor "$flavor" --mode standalone --name "$name" \
                    -o "$work/stage/usr/local/libexec/$name" "$src/$NUITKA_MAIN"
                mkdir -p "$work/stage/usr/local/bin"
                ln -s "../libexec/$name/$name" "$work/stage/usr/local/bin/$name"
                ;;
            *)
                echo "$0: unknown NUITKA_MODE '$NUITKA_MODE'" >&2; exit 2 ;;
        esac
        ;;
    *)
        echo "$0: unknown BUILD '$BUILD' (cmake or nuitka)" >&2; exit 2 ;;
esac

openbsd-pkg-create -v --sysroot "$sysroot" --name "$PKGNAME" --pkgpath "$PKGPATH" \
    --comment "$COMMENT" --desc "$DESC" ${extra_depend:+--depend "$extra_depend"} \
    ${key:+--sign "$key"} -o "$out/$PKGNAME.tgz" "$work/stage/usr/local"
