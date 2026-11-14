#!/usr/bin/env python3
"""Pure-Python implementation of the OpenBSD signify(1) formats needed here.

Covers: key generation (unencrypted secret keys only), embedded-message
verification (SHA256.sig), and gzip-embedded signatures (signify -z), which is
how OpenBSD packages are signed. Only depends on `cryptography` (Ed25519).
"""
import base64
import datetime
import hashlib
import os
import struct
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

PKALG = b"Ed"
KDFALG = b"BK"
COMMENT_PREFIX = "untrusted comment: "
GZ_BLOCKSIZE = 65536
GZ_FCOMMENT = 0x10
# signify writes this fixed header and drops the original one (zsig.c).
GZ_FAKE_HEADER = bytes([0x1F, 0x8B, 8, GZ_FCOMMENT, 0, 0, 0, 0, 0, 3])


class SignifyError(Exception):
    pass


def _parse_b64_file(data, what):
    text = data.decode("ascii", errors="strict") if isinstance(data, bytes) else data
    lines = text.split("\n")
    if len(lines) < 2 or not lines[0].startswith(COMMENT_PREFIX):
        raise SignifyError(f"{what}: missing '{COMMENT_PREFIX.strip()}' line")
    comment = lines[0][len(COMMENT_PREFIX):]
    raw = base64.b64decode(lines[1], validate=True)
    if raw[:2] != PKALG:
        raise SignifyError(f"{what}: unsupported algorithm {raw[:2]!r}")
    consumed = len(lines[0]) + 1 + len(lines[1]) + 1
    return comment, raw, consumed


def _b64_file(comment, raw):
    return f"{COMMENT_PREFIX}{comment}\n{base64.b64encode(raw).decode()}\n".encode()


class PublicKey:
    def __init__(self, keynum, key):
        self.keynum = keynum
        self.key = Ed25519PublicKey.from_public_bytes(key)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            _, raw, _ = _parse_b64_file(f.read(), path)
        if len(raw) != 2 + 8 + 32:
            raise SignifyError(f"{path}: bad public key length {len(raw)}")
        return cls(raw[2:10], raw[10:])

    def verify(self, sigraw, msg):
        if len(sigraw) != 2 + 8 + 64:
            raise SignifyError("bad signature length")
        if sigraw[2:10] != self.keynum:
            raise SignifyError("signature key number does not match public key")
        try:
            self.key.verify(sigraw[10:], msg)
        except InvalidSignature:
            raise SignifyError("signature verification failed") from None


class SecretKey:
    def __init__(self, keynum, seed):
        self.keynum = keynum
        self.priv = Ed25519PrivateKey.from_private_bytes(seed)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            _, raw, _ = _parse_b64_file(f.read(), path)
        if len(raw) != 2 + 2 + 4 + 16 + 8 + 8 + 64 or raw[2:4] != KDFALG:
            raise SignifyError(f"{path}: bad secret key")
        (rounds,) = struct.unpack(">I", raw[4:8])
        if rounds != 0:
            raise SignifyError(f"{path}: passphrase-protected keys are not supported (use signify -n)")
        checksum, keynum, seckey = raw[24:32], raw[32:40], raw[40:104]
        if hashlib.sha512(seckey).digest()[:8] != checksum:
            raise SignifyError(f"{path}: secret key checksum mismatch")
        return cls(keynum, seckey[:32])

    def sign(self, msg):
        return PKALG + self.keynum + self.priv.sign(msg)


def generate(pubpath, secpath, comment="signify"):
    """Equivalent of `signify -G -n -p pubpath -s secpath`."""
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    keynum = os.urandom(8)
    seckey = seed + pub
    secraw = (PKALG + KDFALG + struct.pack(">I", 0) + bytes(16)
              + hashlib.sha512(seckey).digest()[:8] + keynum + seckey)
    fd = os.open(secpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(_b64_file(f"{comment} secret key", secraw))
    with open(pubpath, "xb") as f:
        f.write(_b64_file(f"{comment} public key", PKALG + keynum + pub))


def verify_embedded(pubkey, sigpath):
    """Verify a file produced by `signify -S -e` (e.g. SHA256.sig); returns the message."""
    with open(sigpath, "rb") as f:
        data = f.read()
    sigraw, msg = _split_signed(data, sigpath)
    pubkey.verify(sigraw, msg)
    return msg


def _split_signed(data, what):
    """Split '<comment line>\\n<b64 sig>\\n<message>' into (raw signature, message)."""
    lines = data.split(b"\n", 2)
    if len(lines) < 3:
        raise SignifyError(f"{what}: truncated signature")
    _, sigraw, consumed = _parse_b64_file(lines[0] + b"\n" + lines[1] + b"\n", what)
    return sigraw, data[consumed:]


def split_gzip_header(data):
    """Return (flags, header_end_offset, comment_bytes_or_None) of a gzip member."""
    if len(data) < 10 or data[:3] != b"\x1f\x8b\x08":
        raise SignifyError("not a gzip file")
    flg = data[3]
    i = 10
    comment = None
    if flg & 0x04:
        (xlen,) = struct.unpack("<H", data[i:i + 2])
        i += 2 + xlen
    if flg & 0x08:
        i = data.index(b"\0", i) + 1
    if flg & GZ_FCOMMENT:
        j = data.index(b"\0", i)
        comment = data[i:j]
        i = j + 1
    if flg & 0x02:
        i += 2
    return flg, i, comment


def verify_gzip(pubkey, data):
    """Verify a gzip-embedded signature (`signify -V -z`); returns the signed header fields."""
    _, end, comment = split_gzip_header(data)
    if comment is None:
        raise SignifyError("gzip file is not signed (no comment)")
    sigraw, msg = _split_signed(comment, "gzip comment")
    pubkey.verify(sigraw, msg)
    head, _, hashes = msg.partition(b"\n\n")
    fields = dict(l.split("=", 1) for l in head.decode().split("\n"))
    if fields.get("algorithm") != "SHA512/256":
        raise SignifyError(f"unsupported algorithm {fields.get('algorithm')}")
    bs = int(fields["blocksize"])
    payload = data[end:]
    expected = [h for h in hashes.decode().split("\n") if h]
    blocks = [payload[o:o + bs] for o in range(0, len(payload), bs)] or [b""]
    if len(expected) != len(blocks):
        raise SignifyError(f"block count mismatch ({len(expected)} != {len(blocks)})")
    for n, (h, blk) in enumerate(zip(expected, blocks)):
        if hashlib.new("sha512_256", blk).hexdigest() != h:
            raise SignifyError(f"block {n} hash mismatch")
    return fields


def sign_gzip(seckey, keyname, data, comment, date=None):
    """Equivalent of `signify -S -z -s keyname`: returns the signed gzip bytes."""
    flg, end, oldcomment = split_gzip_header(data)
    if oldcomment is not None:
        raise SignifyError("gzip file already carries a comment/signature")
    payload = data[end:]
    if date is None:
        date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = (f"date={date}\nkey={keyname}\nalgorithm=SHA512/256\nblocksize={GZ_BLOCKSIZE}\n\n").encode()
    for o in range(0, max(len(payload), 1), GZ_BLOCKSIZE):
        msg += hashlib.new("sha512_256", payload[o:o + GZ_BLOCKSIZE]).hexdigest().encode() + b"\n"
    sig = _b64_file(comment, seckey.sign(msg))
    return GZ_FAKE_HEADER + sig + msg + b"\0" + payload


def _main(argv):
    import argparse

    ap = argparse.ArgumentParser(description="Minimal signify(1) work-alike")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="create an unencrypted key pair")
    g.add_argument("-p", required=True)
    g.add_argument("-s", required=True)
    g.add_argument("-c", default="signify")
    v = sub.add_parser("verify-embedded", help="verify SHA256.sig-style file, print message")
    v.add_argument("-p", required=True)
    v.add_argument("-x", required=True)
    z = sub.add_parser("verify-gzip", help="verify a signed .tgz")
    z.add_argument("-p", required=True)
    z.add_argument("file")
    s = sub.add_parser("sign-gzip", help="sign a .tgz in place (or to -o)")
    s.add_argument("-s", required=True)
    s.add_argument("-o")
    s.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "generate":
            generate(a.p, a.s, a.c)
        elif a.cmd == "verify-embedded":
            sys.stdout.buffer.write(verify_embedded(PublicKey.load(a.p), a.x))
        elif a.cmd == "verify-gzip":
            with open(a.file, "rb") as f:
                fields = verify_gzip(PublicKey.load(a.p), f.read())
            print(f"Signature Verified ({fields['key']}, {fields['date']})", file=sys.stderr)
        elif a.cmd == "sign-gzip":
            keyname = os.path.basename(a.s)
            if not keyname.endswith(".sec"):
                raise SignifyError("secret key file must end in .sec")
            with open(a.file, "rb") as f:
                out = sign_gzip(SecretKey.load(a.s), keyname, f.read(),
                                f"verify with {keyname[:-4]}.pub")
            with open(a.o or a.file, "wb") as f:
                f.write(out)
    except (SignifyError, OSError, ValueError) as e:
        print(f"signify.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
