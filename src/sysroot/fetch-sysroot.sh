#!/bin/sh
# Build an OpenBSD/amd64 sysroot from the official, signify-verified install sets.
#
# usage: fetch-sysroot.sh <release|current> <destdir> [sets...]
#   release : e.g. 8.0   (uses pub/OpenBSD/8.0/amd64)
#   current : snapshots  (uses pub/OpenBSD/snapshots/amd64)
#   sets    : default "base comp" ; add "xbase" for X11 headers/libs
# env: OPENBSD_MIRROR (default https://cdn.openbsd.org/pub/OpenBSD)
#      CACHE_DIR      (default <destdir>/../cache)
set -eu

die() { echo "fetch-sysroot: $*" >&2; exit 1; }

[ $# -ge 2 ] || die "usage: $0 <release|current> <destdir> [sets...]"
FLAVOR=$1; DEST=$2; shift 2
SETS=${*:-base comp}
MIRROR=${OPENBSD_MIRROR:-https://cdn.openbsd.org/pub/OpenBSD}
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SRC=$(cd "$HERE/.." && pwd)
SIGNIFY="python3 $SRC/pkgtools/signify.py"

case $FLAVOR in
    current) URL=$MIRROR/snapshots/amd64 ;;
    [0-9].[0-9]) URL=$MIRROR/$FLAVOR/amd64 ;;
    *) die "flavor must be a release (e.g. 8.0) or 'current'" ;;
esac

mkdir -p "$DEST"
DEST=$(cd "$DEST" && pwd)
CACHE=${CACHE_DIR:-$DEST/../cache/$FLAVOR}
mkdir -p "$CACHE"

echo "==> fetching SHA256.sig from $URL"
curl -fsS -o "$CACHE/SHA256.sig.new" "$URL/SHA256.sig"
KEY=$(sed -n '1s/^untrusted comment: verify with \(openbsd-[0-9]*-base\)\.pub$/\1/p' "$CACHE/SHA256.sig.new")
[ -n "$KEY" ] || die "unexpected SHA256.sig comment line"
[ -f "$SRC/keys/$KEY.pub" ] || die "no trusted key $KEY.pub in $SRC/keys (add it from a trusted source)"
$SIGNIFY verify-embedded -p "$SRC/keys/$KEY.pub" -x "$CACHE/SHA256.sig.new" > "$CACHE/SHA256.new" \
    || die "SHA256.sig signature verification FAILED"
mv "$CACHE/SHA256.sig.new" "$CACHE/SHA256.sig"
mv "$CACHE/SHA256.new" "$CACHE/SHA256"

# Set version suffix (e.g. 80) taken from the verified checksum list itself.
VER=$(sed -n 's/^SHA256 (base\([0-9]*\)\.tgz) = .*/\1/p' "$CACHE/SHA256")
[ -n "$VER" ] || die "cannot find base set in SHA256"

for s in $SETS; do
    f=$s$VER.tgz
    want=$(sed -n "s/^SHA256 ($f) = \([0-9a-f]*\)\$/\1/p" "$CACHE/SHA256")
    [ -n "$want" ] || die "$f not listed in SHA256"
    have=$( [ -f "$CACHE/$f" ] && sha256sum "$CACHE/$f" | cut -d' ' -f1 || true)
    if [ "$have" != "$want" ]; then
        echo "==> downloading $f"
        curl -fsS -o "$CACHE/$f.part" "$URL/$f"
        mv "$CACHE/$f.part" "$CACHE/$f"
        have=$(sha256sum "$CACHE/$f" | cut -d' ' -f1)
        [ "$have" = "$want" ] || { rm -f "$CACHE/$f"; die "$f checksum mismatch"; }
    fi
    echo "==> extracting $f (checksum verified)"
    # Only what a cross compiler/linker needs: skip programs, man pages, kernels.
    tar -xzf "$CACHE/$f" -C "$DEST" --no-same-owner \
        --exclude='./bsd*' --exclude='./bin' --exclude='./sbin' --exclude='./var' \
        --exclude='./usr/bin' --exclude='./usr/sbin' --exclude='./usr/share' \
        --exclude='./usr/libexec' --exclude='./usr/games' --exclude='./usr/X11R6/bin' \
        --exclude='./usr/X11R6/man' --exclude='./usr/X11R6/share' --exclude='./usr/local'
done

[ -f "$DEST/usr/lib/crt0.o" ] || die "sysroot incomplete: usr/lib/crt0.o missing (did you fetch 'comp'?)"
ls "$DEST"/usr/lib/libc.so.* >/dev/null 2>&1 || die "sysroot incomplete: libc.so.* missing (did you fetch 'base'?)"

# OpenBSD's linker picks the highest libfoo.so.MAJOR.MINOR for -lfoo; upstream lld
# only looks for libfoo.so, so provide unversioned links to the newest version.
for d in "$DEST/usr/lib" "$DEST/usr/X11R6/lib" "$DEST/usr/local/lib"; do
    if [ -d "$d" ]; then "$HERE/link-versioned-libs.sh" "$d"; fi
done

{
    echo "flavor=$FLAVOR"
    echo "version=$VER"
    echo "url=$URL"
    echo "sets=$SETS"
    curl -fsS "$URL/BUILDINFO" 2>/dev/null | sed 's/^/buildinfo=/' || true
} > "$DEST/.sysroot-info"
echo "==> sysroot ready in $DEST"
cat "$DEST/.sysroot-info"
