#!/bin/sh
# Install the cross toolchain front-end for one or more flavors.
# usage: install.sh <prefix> <flavor>...   (e.g. install.sh /opt/openbsd-cross 8.0 current)
#
# Layout produced:
#   <prefix>/bin/openbsd-cross + x86_64-unknown-openbsd<flavor>-<tool> symlinks
#   <prefix>/share/openbsd-cross/{openbsd-toolchain.cmake,openbsd-<flavor>.ini,config.site}
#   <prefix>/libexec/openbsd-cross/{pkgtools,sysroot,keys}  (pkg_create.py, pkg_fetch.py, ...)
# Sysroots are expected in <prefix>/sysroot/<flavor> (src/sysroot/fetch-sysroot.sh).
set -eu
[ $# -ge 2 ] || { echo "usage: $0 <prefix> <flavor>..." >&2; exit 2; }
mkdir -p "$1"
prefix=$(cd "$1" && pwd); shift
src=$(cd "$(dirname "$0")/.." && pwd)
share=$prefix/share/openbsd-cross
libexec=$prefix/libexec/openbsd-cross

mkdir -p "$prefix/bin" "$share" "$libexec"
install -m 0755 "$src/wrappers/openbsd-cross" "$prefix/bin/openbsd-cross"
install -m 0644 "$src/cmake/openbsd-toolchain.cmake" "$src/autotools/config.site" "$share/"
for d in pkgtools sysroot keys; do
    rm -rf "${libexec:?}/$d"
    cp -R "$src/$d" "$libexec/$d"
done
rm -rf "$libexec/pkgtools/__pycache__"
for t in pkg_create pkg_fetch signify; do
    ln -sfn "../libexec/openbsd-cross/pkgtools/$t.py" "$prefix/bin/openbsd-$(echo $t | tr _ -)"
done
ln -sfn ../libexec/openbsd-cross/sysroot/fetch-sysroot.sh "$prefix/bin/openbsd-fetch-sysroot"

for flavor in "$@"; do
    case $flavor in
        current|[0-9].[0-9]) ;;
        *) echo "install.sh: bad flavor '$flavor' (want e.g. 8.0 or current)" >&2; exit 2 ;;
    esac
    for tool in cc c++ clang clang++ cpp ld ar nm ranlib strip objcopy objdump readelf size \
                strings pkg-config sysroot; do
        ln -sfn openbsd-cross "$prefix/bin/x86_64-unknown-openbsd$flavor-$tool"
    done
    sed -e "s|@FLAVOR@|$flavor|g" -e "s|@BINDIR@|$prefix/bin|g" \
        -e "s|@SYSROOT@|$prefix/sysroot/$flavor|g" \
        "$src/meson/openbsd-cross.ini.in" > "$share/openbsd-$flavor.ini"
done
echo "installed OpenBSD cross front-end for: $* in $prefix"
