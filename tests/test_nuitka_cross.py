"""Unit tests for src/nuitka/nuitka_cross.py (run: python3 -m unittest discover -s tests).
They cover the parsing logic only; Nuitka itself is exercised by tests/build-example.sh."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "nuitka"))
import nuitka_cross  # noqa: E402

NUITKA_DIR = "/opt/nuitka/lib/nuitka"
COMPILE_LINE = (
    '"/usr/bin/ccache" "/usr/bin/gcc" -o module.__main__.o -c -std=c11 -flto -fwrapv -O3 '
    "-D_NUITKA_EXE_MODE -D__NUITKA_NO_ASSERT__ -D_NUITKA_CONSTANTS_FROM_CODE "
    "-D_NUITKA_ADAPTED_PYTHON_HEADERS -D_NUITKA_FILE_REFERENCE_RUNTIME_MODE "
    f"-I{NUITKA_DIR}/build/inline_copy/zlib -I/home/u/.cache/Nuitka/adapted_headers/0123abcd "
    "-I/usr/include/python3.14 -I/usr/include/python3.14/internal/mimalloc -I. "
    f"-I{NUITKA_DIR}/build/include -I{NUITKA_DIR}/build/static_src module.__main__.c")


class ParseTest(unittest.TestCase):
    def test_patchlevel(self):
        text = '#define PY_MINOR_VERSION 14\n#define PY_VERSION      "3.14.7+"\n'
        self.assertEqual(nuitka_cross.parse_patchlevel(text), "3.14.7")
        with self.assertRaises(nuitka_cross.CrossError):
            nuitka_cross.parse_patchlevel("#define PY_MAJOR_VERSION 3\n")

    def test_gil_disabled(self):
        self.assertTrue(nuitka_cross.gil_disabled("#define Py_GIL_DISABLED 1\n"))
        self.assertFalse(nuitka_cross.gil_disabled("/* #undef Py_GIL_DISABLED */\n"))

    def test_compile_line(self):
        with tempfile.TemporaryDirectory() as inc:
            os.makedirs(os.path.join(inc, "internal", "mimalloc"))
            lines = ["Nuitka-Scons: Backend C compiler: gcc", COMPILE_LINE, "something else"]
            flags = nuitka_cross.parse_compile_line(lines, NUITKA_DIR, inc)
        self.assertEqual(flags, [
            "-std=c11", "-D_NUITKA_EXE_MODE", "-D__NUITKA_NO_ASSERT__",
            "-D_NUITKA_CONSTANTS_FROM_CODE", "-D_NUITKA_FILE_REFERENCE_RUNTIME_MODE",
            f"-I{NUITKA_DIR}/build/inline_copy/zlib", f"-I{inc}",
            f"-I{inc}/internal/mimalloc", "-I.",
            f"-I{NUITKA_DIR}/build/include", f"-I{NUITKA_DIR}/build/static_src"])

    def test_compile_line_missing(self):
        with self.assertRaises(nuitka_cross.CrossError):
            nuitka_cross.parse_compile_line(["no compiler here"], NUITKA_DIR, "/x")

    def test_link_sources(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "@link_input.txt"), "w") as f:
                f.write('"__constants.o"\n"module.__main__.o"\n"static_src/MainProgram.o"\n')
            self.assertEqual(nuitka_cross.link_sources(d),
                             ["__constants.c", "module.__main__.c", "static_src/MainProgram.c"])
            with open(os.path.join(d, "@link_input.txt"), "w") as f:
                f.write('"libfoo.a"\n')
            with self.assertRaises(nuitka_cross.CrossError):
                nuitka_cross.link_sources(d)

    def test_drop_host_definitions(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "build_definitions.h")
            with open(path, "w") as f:
                f.write('#define NO_PYTHON_WARNINGS 0\n'
                        '#define PYTHON_HOME_PATH "/opt/uv/cpython-3.14.7-linux-x86_64-gnu"\n'
                        '#define SYSFLAG_UTF8 0\n')
            nuitka_cross.drop_host_definitions(d)
            with open(path) as f:
                self.assertEqual(f.read(), "#define NO_PYTHON_WARNINGS 0\n#define SYSFLAG_UTF8 0\n")
            os.unlink(path)
            with self.assertRaises(nuitka_cross.CrossError):
                nuitka_cross.drop_host_definitions(d)

    def test_depend_from_plist(self):
        contents = ("@name python-3.14.7p0\n@comment pkgpath=lang/python/3,-main ftp=yes\n"
                    "@arch amd64\n@cwd /usr/local\nbin/python3.14\n")
        self.assertEqual(nuitka_cross.depend_from_plist(contents, "3.14"),
                         "lang/python/3,-main:python->=3.14:python-3.14.7p0")
        with self.assertRaises(nuitka_cross.CrossError):
            nuitka_cross.depend_from_plist("@name python-3.14.7p0\n", "3.14")

    def test_interpreter_link_flags(self):
        elf = nuitka_cross.elfinfo
        self.assertEqual(
            nuitka_cross.interpreter_link_flags({1, elf.PT_OPENBSD_NOBTCFI, elf.PT_OPENBSD_WXNEEDED}),
            ["-Wl,-z,wxneeded", "-Wl,-z,nobtcfi"])
        self.assertEqual(nuitka_cross.interpreter_link_flags({1, 2, 3}), [])

    def test_extra_options(self):
        nuitka_cross.check_extra_options(["--jobs=2", "--nofollow-import-to=foo"])
        for bad in ("--mode=standalone", "--onefile", "--output-dir=/x", "--lto=yes",
                    "--report=r.xml"):
            with self.assertRaises(nuitka_cross.CrossError):
                nuitka_cross.check_extra_options([bad])

    def test_nuitka_command(self):
        cmd = nuitka_cross.nuitka_command("py", "/s/main.py", "/w/host", ["--jobs=2"])
        self.assertEqual(cmd[:3], ["py", "-m", "nuitka"])
        self.assertIn("--mode=accelerated", cmd)
        self.assertIn(f"--user-plugin={nuitka_cross.PLUGIN}", cmd)
        self.assertEqual(cmd[-2:], ["--jobs=2", "/s/main.py"])
        self.assertIn("--report=/w/host/report.xml", cmd)
        cmd = nuitka_cross.nuitka_command("py", "/s/main.py", "/w/host", [], "standalone")
        self.assertIn("--mode=standalone", cmd)

    def test_target_sysconfig_modules(self):
        with tempfile.TemporaryDirectory() as d:
            pylib = os.path.join(d, "usr/local/lib/python3.14")
            os.makedirs(pylib)
            with self.assertRaises(nuitka_cross.CrossError):
                nuitka_cross.target_sysconfig_modules(d, "3.14", os.path.join(d, "mods"))
            with open(os.path.join(pylib, "_sysconfigdata__openbsd8_.py"), "w") as f:
                f.write("build_time_vars = {}\n")
            self.assertEqual(
                nuitka_cross.target_sysconfig_modules(d, "3.14", os.path.join(d, "mods")),
                ["_sysconfigdata__openbsd8_"])
            self.assertTrue(os.path.isfile(os.path.join(d, "mods/_sysconfigdata__openbsd8_.py")))


if __name__ == "__main__":
    unittest.main()
