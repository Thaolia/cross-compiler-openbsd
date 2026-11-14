"""Unit tests for src/pkgtools (run: python3 -m unittest discover -s tests)."""
import gzip
import io
import os
import sys
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "pkgtools"))
import pkgcommon  # noqa: E402
import signify  # noqa: E402


class SignifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pub = os.path.join(self.tmp.name, "unit-pkg.pub")
        self.sec = os.path.join(self.tmp.name, "unit-pkg.sec")
        signify.generate(self.pub, self.sec, "unit")
        self.payload = gzip.compress(os.urandom(200_000), mtime=0)

    def tearDown(self):
        self.tmp.cleanup()

    def signed(self):
        return signify.sign_gzip(signify.SecretKey.load(self.sec), "unit-pkg.sec",
                                 self.payload, "verify with unit-pkg.pub")

    def test_roundtrip(self):
        data = self.signed()
        fields = signify.verify_gzip(signify.PublicKey.load(self.pub), data)
        self.assertEqual(fields["key"], "unit-pkg.sec")
        self.assertEqual(fields["blocksize"], "65536")
        # Still a valid gzip stream with the same content.
        self.assertEqual(gzip.decompress(data), gzip.decompress(self.payload))

    def test_tampered_payload_rejected(self):
        data = bytearray(self.signed())
        data[-5000] ^= 0xFF
        with self.assertRaises(signify.SignifyError):
            signify.verify_gzip(signify.PublicKey.load(self.pub), bytes(data))

    def test_wrong_key_rejected(self):
        other_pub = os.path.join(self.tmp.name, "o.pub")
        signify.generate(other_pub, os.path.join(self.tmp.name, "o.sec"))
        with self.assertRaises(signify.SignifyError):
            signify.verify_gzip(signify.PublicKey.load(other_pub), self.signed())

    def test_double_sign_refused(self):
        with self.assertRaises(signify.SignifyError):
            signify.sign_gzip(signify.SecretKey.load(self.sec), "unit-pkg.sec",
                              self.signed(), "x")

    def test_real_openbsd_set_signature(self):
        """Verify a real OpenBSD 8.0 SHA256.sig when the cached sysroot has one."""
        sig = os.path.join(ROOT, "build/sysroot/cache/8.0/SHA256.sig")
        if not os.path.exists(sig):
            self.skipTest("no cached OpenBSD 8.0 SHA256.sig")
        key = signify.PublicKey.load(os.path.join(ROOT, "src/keys/openbsd-80-base.pub"))
        self.assertIn(b"base80.tgz", signify.verify_embedded(key, sig))


class UstarTest(unittest.TestCase):
    def test_tarfile_reads_our_archive(self):
        long_name = "share/doc/x/" + "d" * 120 + "/file.txt"
        archive = (pkgcommon.ustar_member("+CONTENTS", b"@name x-1\n", mode=0o444,
                                          owner=pkgcommon.META_OWNER, typebits=False)
                   + pkgcommon.ustar_member("bin/x", b"\x7fELF...", mode=0o755)
                   + pkgcommon.ustar_member("bin/y", mode=0o755, symlink="x")
                   + pkgcommon.ustar_member(long_name, b"hi")
                   + pkgcommon.USTAR_END)
        with tarfile.open(fileobj=io.BytesIO(archive)) as t:
            m = {i.name: i for i in t.getmembers()}
            self.assertEqual(list(m)[0], "+CONTENTS")
            self.assertEqual((m["bin/x"].uname, m["bin/x"].gname, m["bin/x"].gid), ("root", "bin", 7))
            self.assertEqual(m["+CONTENTS"].gname, "wheel")
            self.assertTrue(m["bin/y"].issym())
            self.assertEqual(m["bin/y"].linkname, "x")
            self.assertEqual(t.extractfile(m[long_name]).read(), b"hi")

    def test_too_long_name_refused(self):
        with self.assertRaises(ValueError):
            pkgcommon.ustar_header("a" * 300)


class MiscTest(unittest.TestCase):
    def test_split_pkgname(self):
        self.assertEqual(pkgcommon.split_pkgname("sdl2-2.32.10p1"), ("sdl2", "2.32.10p1", ""))
        self.assertEqual(pkgcommon.split_pkgname("vim-9.1.1-gtk3"), ("vim", "9.1.1", "-gtk3"))
        with self.assertRaises(ValueError):
            pkgcommon.split_pkgname("noversion")

    def test_mtree(self):
        with tempfile.NamedTemporaryFile("w", suffix=".dist", delete=False) as f:
            f.write("/set type=dir\n.\nusr\n    local\n        bin\n        ..\n    ..\n..\nvar\n..\n")
        try:
            self.assertEqual(pkgcommon.mtree_dirs(f.name),
                             {"/", "/usr", "/usr/local", "/usr/local/bin", "/var"})
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
