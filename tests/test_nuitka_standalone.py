"""Unit tests for src/nuitka/standalone.py and the @so rule of pkg_create.py
(run: python3 -m unittest discover -s tests)."""
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "pkgtools"))
sys.path.insert(0, os.path.join(ROOT, "src", "nuitka"))
import pkg_create  # noqa: E402
import standalone  # noqa: E402

REPORT = """<?xml version='1.0' encoding='utf8'?>
<nuitka-compilation-report nuitka_version="4.3.5" mode="standalone" completion="yes">
  <module name="__main__" kind="PythonMainModule" usage="root_module" source_path="m.py">
    <module_usages>
      <module_usage name="math" finding="absolute" line="1" reason="import" />
      <module_usage name="pydoc" finding="excluded" line="2" reason="import" />
      <module_usage name="_testcapi" finding="not-found" line="3" reason="import" />
    </module_usages>
  </module>
  <module name="_sqlite3" kind="PythonExtensionModule" usage="import"
    source_path="${sys.prefix}/lib/python3.14/lib-dynload/_sqlite3.cpython-314-x86_64-linux-gnu.so" />
  <module name="_onlylinux" kind="PythonExtensionModule" usage="import"
    source_path="${sys.prefix}/lib/python3.14/lib-dynload/_onlylinux.cpython-314-x86_64-linux-gnu.so" />
  <module name="pkg.fast" kind="PythonExtensionModule" usage="import"
    source_path="/venv/site-packages/pkg/fast.cpython-314-x86_64-linux-gnu.so" />
</nuitka-compilation-report>
"""


def fake_elf(path, etype=3, needed=()):
    """Minimal ELF64 LE file: header, one PT_LOAD and a PT_DYNAMIC with DT_NEEDED entries."""
    strtab = b"\0" + b"".join(n.encode() + b"\0" for n in needed)
    offs, o = [], 1
    for n in needed:
        offs.append(o)
        o += len(n) + 1
    phoff, phnum = 64, 2
    dyn_off = phoff + 56 * phnum
    dyn = b"".join(struct.pack("<qQ", 1, x) for x in offs)
    str_off = dyn_off + len(dyn) + 32
    dyn += struct.pack("<qQ", 5, str_off) + struct.pack("<qQ", 0, 0)
    size = str_off + len(strtab)
    head = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9
    head += struct.pack("<HHIQQQIHHHHHH", etype, 62, 1, 0, phoff, 0, 0, 64, 56, phnum, 64, 0, 0)
    ph = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, size, size, 0x1000)
    ph += struct.pack("<IIQQQQQQ", 2, 6, dyn_off, dyn_off, dyn_off, len(dyn), len(dyn), 8)
    data = head + ph + dyn + strtab
    with open(path, "wb") as f:
        f.write(data)


class StandaloneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.sysroot = os.path.join(self.root, "sysroot")
        pylib = os.path.join(self.sysroot, "usr/local/lib/python3.14")
        self.dynload = os.path.join(pylib, "lib-dynload")
        os.makedirs(self.dynload)
        os.makedirs(os.path.join(pylib, "site-packages/pkg"))
        for m in ("_sqlite3", "math", "zlib", "_testcapi"):
            fake_elf(os.path.join(self.dynload, f"{m}.cpython-314.so"))
        fake_elf(os.path.join(pylib, "site-packages/pkg/fast.abi3.so"))
        self.report = os.path.join(self.root, "report.xml")
        with open(self.report, "w") as f:
            f.write(REPORT)
        self.warnings = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_report(self):
        exts, used = standalone.parse_report(self.report)
        self.assertEqual(sorted(exts), ["_onlylinux", "_sqlite3", "pkg.fast"])
        self.assertIn("math", used)
        self.assertIn("_testcapi", used)
        self.assertNotIn("pydoc", used)

    def test_incomplete_report(self):
        with open(self.report, "w") as f:
            f.write(REPORT.replace('completion="yes"', 'completion="no"'))
        with self.assertRaises(standalone.StandaloneError):
            standalone.parse_report(self.report)

    def test_plan_extensions(self):
        exts, used = standalone.parse_report(self.report)
        plan = standalone.plan_extensions(self.sysroot, "3.14", exts, used, self.warnings.append)
        self.assertEqual(sorted(plan), ["_sqlite3.so", "math.so", "pkg/fast.so"])
        self.assertTrue(plan["pkg/fast.so"].endswith("site-packages/pkg/fast.abi3.so"))
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("_onlylinux", self.warnings[0])

    def test_missing_third_party_extension(self):
        os.unlink(os.path.join(self.sysroot, "usr/local/lib/python3.14/site-packages/pkg/fast.abi3.so"))
        exts, used = standalone.parse_report(self.report)
        with self.assertRaises(standalone.StandaloneError):
            standalone.plan_extensions(self.sysroot, "3.14", exts, used, self.warnings.append)

    def test_bundle_libraries(self):
        for d in ("usr/lib", "usr/local/lib"):
            os.makedirs(os.path.join(self.sysroot, d), exist_ok=True)
        fake_elf(os.path.join(self.sysroot, "usr/lib/libc.so.104.0"))
        fake_elf(os.path.join(self.sysroot, "usr/local/lib/libiconv.so.7.2"),
                 needed=["libc.so.104.0"])
        fake_elf(os.path.join(self.sysroot, "usr/local/lib/libintl.so.8.2"),
                 needed=["libiconv.so.7.2", "libc.so.104.0"])
        exe = os.path.join(self.root, "exe")
        fake_elf(exe, needed=["libintl.so.8.2", "libc.so.104.0"])
        self.assertEqual(sorted(standalone.bundle_libraries(self.sysroot, [exe])),
                         ["libiconv.so.7.2", "libintl.so.8.2"])
        fake_elf(exe, needed=["libmissing.so.1.0"])
        with self.assertRaises(standalone.StandaloneError):
            standalone.bundle_libraries(self.sysroot, [exe])

    def test_host_data_files(self):
        dist = os.path.join(self.root, "dist")
        os.makedirs(os.path.join(dist, "pkg/data"))
        fake_elf(os.path.join(dist, "prog.bin"), etype=2)
        fake_elf(os.path.join(dist, "_sqlite3.so"))
        fake_elf(os.path.join(dist, "libsqlite3.so.0"))
        fake_elf(os.path.join(dist, "pkg/helper"), etype=2)
        with open(os.path.join(dist, "pkg/data/table.txt"), "w") as f:
            f.write("x")
        data = standalone.host_data_files(dist, os.path.join(dist, "prog.bin"), {"_sqlite3.so"},
                                          self.warnings.append)
        self.assertEqual(data, ["pkg/data/table.txt"])
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("pkg/helper", self.warnings[0])


class PkgCreateSoTest(unittest.TestCase):
    """Shared objects outside lib/ are @so (as in the official python package), not @lib."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.builder = pkg_create.PackageBuilder.__new__(pkg_create.PackageBuilder)
        self.builder.entries, self.builder.members = [], []
        self.builder.own_libs, self.builder.needed = set(), {}

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, rel):
        path = os.path.join(self.tmp.name, os.path.basename(rel))
        fake_elf(path)
        self.builder.add_file(path, rel)
        return self.builder.entries[-4]

    def test_annotations(self):
        self.assertEqual(self.add("lib/libfoo.so.1.0"), "@lib lib/libfoo.so.1.0")
        self.assertEqual(self.add("libexec/p/libpython3.14.so.0.0"),
                         "@so libexec/p/libpython3.14.so.0.0")
        self.assertEqual(self.add("libexec/p/zlib.so"), "@so libexec/p/zlib.so")
        self.assertEqual(self.add("lib/python3.14/site-packages/x.cpython-314.so"),
                         "@so lib/python3.14/site-packages/x.cpython-314.so")
        self.assertEqual(self.builder.own_libs, {"libfoo.so.1.0", "libpython3.14.so.0.0"})
        with self.assertRaises(pkg_create.PkgError):
            self.add("lib/libbar.so")


if __name__ == "__main__":
    unittest.main()
