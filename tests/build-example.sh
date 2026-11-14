#!/bin/sh
# End-to-end example: fetch dependencies, cross-build with CMake, create an OpenBSD package.
# usage: build-example.sh <example-dir> <flavor> <outdir> [signing-key-pkg.sec]
# <example-dir>/pkg.conf defines PKGNAME PKGPATH COMMENT DESC DEPS (official packages).
# Needs the front-end on PATH (docker image, or `src/wrappers/install.sh <prefix> <flavor>`).
set -eu
[ $# -ge 3 ] || { echo "usage: $0 <example-dir> <flavor> <outdir> [key.sec]" >&2; exit 2; }
src=$(cd "$1" && pwd)
flavor=$2
out=$(mkdir -p "$3" && cd "$3" && pwd)
key=${4:-}
PKGNAME= PKGPATH= COMMENT= DESC= DEPS=
. "$src/pkg.conf"
t=x86_64-unknown-openbsd$flavor
sysroot=$($t-sysroot)
bindir=$(dirname "$(command -v "$t-cc")")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if [ -n "$DEPS" ]; then
    # shellcheck disable=SC2086
    openbsd-pkg-fetch --sysroot "$sysroot" $DEPS
fi
cmake -S "$src" -B "$work/build" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$bindir/../share/openbsd-cross/openbsd-toolchain.cmake" \
    -DOPENBSD_FLAVOR="$flavor" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build "$work/build"
DESTDIR="$work/stage" cmake --install "$work/build" --strip

openbsd-pkg-create -v --sysroot "$sysroot" --name "$PKGNAME" --pkgpath "$PKGPATH" \
    --comment "$COMMENT" --desc "$DESC" \
    ${key:+--sign "$key"} -o "$out/$PKGNAME.tgz" "$work/stage/usr/local"
