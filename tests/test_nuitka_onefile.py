"""Unit tests for src/nuitka/onefile.py (run: python3 -m unittest discover -s tests).
The packing test runs Nuitka's own payload packer when Nuitka is in build/nuitka/lib."""
import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "nuitka"))
import nuitka_cross  # noqa: E402
import onefile  # noqa: E402

NUITKA_LIB = os.path.join(ROOT, "build", "nuitka", "lib")
DEFINITIONS = """\
#define _NUITKA_ONEFILE_ARCHIVE_BOOL 0
#define _NUITKA_ONEFILE_CHILD_GRACE_TIME_INT 5000
#define _NUITKA_ONEFILE_COMPRESSION_BOOL 1
#define _NUITKA_ONEFILE_HAS_PAYLOAD_BOOL 1
#define _NUITKA_ONEFILE_PAYLOAD_SIZE_INT 27272
#define _NUITKA_ONEFILE_TEMP_SPEC "{TEMP}/onefile_{PID}_{TIME_US}_{RANDOM}"
"""
NUITKA_DIR = "/opt/nuitka/lib/nuitka"
SCONS_OUTPUT = [
    f"Nuitka-Scons: Scons command: python3 scons.py -f {NUITKA_DIR}/build/Backend.scons",
    '"/usr/bin/gcc" -o module.__main__.o -c -std=c11 -D_NUITKA_EXE_MODE module.__main__.c',
    f"Nuitka-Scons: Scons command: python3 scons.py -f {NUITKA_DIR}/build/Onefile.scons",
    '"/usr/bin/ccache" "/usr/bin/gcc" -o __constants_data___payload.o -c -std=c11 -O3 '
    "-D_NUITKA_ONEFILE_MODE -D_NUITKA_ONEFILE_TEMP_BOOL -D_NUITKA_CONSTANTS_FROM_CODE "
    f"-I{NUITKA_DIR}/build/inline_copy/zstd -I. -I{NUITKA_DIR}/build/include "
    "__constants_data___payload.c",
    '"/usr/bin/gcc" -o static_src/OnefileBootstrap.o -c -std=c11 -D_NUITKA_ONEFILE_MODE '
    "-D_NUITKA_ONEFILE_TEMP_BOOL static_src/OnefileBootstrap.c",
    '"/usr/bin/gcc" -o _nuitka_temp.bin -s -z noexecstack __constants_data___payload.o',
]


def read_payload(path):
    """Decode Nuitka's onefile payload (non-archive mode): [(name, flags, data)]."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:2] == b"KA"
    assert struct.unpack("<Q", data[-8:])[0] == len(data) - 8
    body = data[3:-8]
    if data[2:3] == b"Y":
        from compression import zstd
        body = zstd.decompress(body)
    stream, entries = io.BytesIO(body), []

    def cstring():
        s = b""
        while (c := stream.read(1)) != b"\0":
            s += c
        return s.decode()
    while name := cstring():
        flags = stream.read(1)[0]
        size = struct.unpack("<Q", stream.read(8))[0]
        entries.append((name, flags, stream.read(size)))
    assert stream.read() == b""
    return entries


class OnefileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.build = os.path.join(self.tmp.name, "prog.onefile-build")
        os.makedirs(os.path.join(self.build, "blobs"))
        with open(os.path.join(self.build, onefile.DEFINITIONS), "w") as f:
            f.write(DEFINITIONS)
        with open(os.path.join(self.build, onefile.PAYLOAD), "wb") as f:
            f.write(b"host payload")

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_definitions(self):
        defs = onefile.read_definitions(self.build)
        self.assertEqual(defs["_NUITKA_ONEFILE_PAYLOAD_SIZE_INT"], "27272")
        self.assertEqual(defs["_NUITKA_ONEFILE_TEMP_SPEC"],
                         '"{TEMP}/onefile_{PID}_{TIME_US}_{RANDOM}"')
        with self.assertRaises(onefile.OnefileError):
            onefile.read_definitions(self.tmp.name)

    def test_set_payload_size(self):
        onefile.set_payload_size(self.build, 123)
        defs = onefile.read_definitions(self.build)
        self.assertEqual(defs["_NUITKA_ONEFILE_PAYLOAD_SIZE_INT"], "123")
        self.assertEqual(defs["_NUITKA_ONEFILE_COMPRESSION_BOOL"], "1")

    def test_bootstrap_compile_lines(self):
        compiles, sources = onefile.bootstrap_compile_lines(SCONS_OUTPUT)
        self.assertEqual(sources, ["__constants_data___payload.c", "static_src/OnefileBootstrap.c"])
        flags = nuitka_cross.parse_compile_line(compiles, NUITKA_DIR, "/nonexistent")
        self.assertIn(f"-I{NUITKA_DIR}/build/inline_copy/zstd", flags)
        self.assertTrue(onefile.temp_mode(flags))
        self.assertFalse(onefile.temp_mode(["-D_NUITKA_ONEFILE_MODE"]))
        with self.assertRaises(onefile.OnefileError):
            onefile.bootstrap_compile_lines(SCONS_OUTPUT[:2])

    @unittest.skipUnless(os.path.isdir(os.path.join(NUITKA_LIB, "nuitka")),
                         "Nuitka not installed in build/nuitka/lib")
    def test_pack_payload(self):
        dist = os.path.join(self.tmp.name, "dist")
        os.makedirs(os.path.join(dist, "pkg"))
        files = {"prog": (b"\x7fELF main", 0o755), "aaa.so": (b"\x7fELF ext", 0o755),
                 "pkg/data.txt": (b"data", 0o644)}
        for rel, (content, mode) in files.items():
            path = os.path.join(dist, rel)
            with open(path, "wb") as f:
                f.write(content)
            os.chmod(path, mode)
        env = dict(os.environ, PYTHONPATH=NUITKA_LIB)
        try:
            subprocess.run([sys.executable, "-c", "from compression import zstd"], check=True,
                           capture_output=True)
        except subprocess.CalledProcessError:
            self.skipTest("this Python has no compression.zstd")
        size = onefile.pack_payload(sys.executable, env, self.build, dist,
                                    os.path.join(dist, "prog"), True, 1)
        payload = os.path.join(self.build, onefile.PAYLOAD)
        self.assertEqual(size, os.path.getsize(payload))
        self.assertEqual(onefile.read_definitions(self.build)["_NUITKA_ONEFILE_PAYLOAD_SIZE_INT"],
                         str(size))
        entries = read_payload(payload)
        # The main program first (the bootstrap runs the first file), then the others.
        self.assertEqual(entries[0], ("prog", 1, b"\x7fELF main"))
        self.assertEqual(sorted(entries[1:]), [("aaa.so", 1, b"\x7fELF ext"),
                                               ("pkg/data.txt", 0, b"data")])


if __name__ == "__main__":
    unittest.main()
