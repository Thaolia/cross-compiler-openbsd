"""Nuitka cross-compilation demo: compiled code calling a compiled local module and the
standard library, C extension modules included (zlib is built into most Linux Pythons but is
a separate extension module in OpenBSD's python package)."""
import hashlib
import json
import sqlite3
import sys
import sysconfig
import zlib

import demolib


def main():
    db = sqlite3.connect(":memory:")
    db.execute("create table t (word text)")
    db.executemany("insert into t values (?)", [(w,) for w in demolib.WORDS])
    words = [row[0] for row in db.execute("select word from t order by word")]
    report = {
        "compiled": "__compiled__" in globals() and demolib.is_compiled(),
        "python": "%d.%d.%d" % sys.version_info[:3],
        "executable": sys.executable,
        "prefix": sys.prefix,
        "soabi": sysconfig.get_config_var("SOABI"),
        "words": words,
        "sha256": hashlib.sha256(" ".join(words).encode()).hexdigest()[:16],
        "crc32": zlib.crc32(" ".join(words).encode()),
        "fib30": demolib.fib(30),
    }
    print(json.dumps(report, indent=1))
    return 0 if report["compiled"] else 1


if __name__ == "__main__":
    sys.exit(main())
