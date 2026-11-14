#!/bin/sh
# For every libfoo.so.MAJOR.MINOR in <dir>, create libfoo.so -> highest version.
# Mirrors the -lfoo lookup of OpenBSD's ld, which upstream lld does not do.
set -eu
dir=$1
cd "$dir"
for lib in $(ls | sed -n 's/^\(lib.*\.so\)\.[0-9]*\.[0-9]*$/\1/p' | sort -u); do
    # Version fields counted from the end so dots inside library names don't matter.
    best=$(ls | grep -E "^$(printf '%s' "$lib" | sed 's/[.+]/\\&/g')\.[0-9]+\.[0-9]+\$" \
        | awk -F. '{ printf "%010d %010d %s\n", $(NF-1), $NF, $0 }' | sort | tail -n 1 | cut -d' ' -f3)
    if [ -n "$best" ]; then ln -sfn "$best" "$lib"; fi
done
