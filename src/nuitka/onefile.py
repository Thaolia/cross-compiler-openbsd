"""Onefile layout for nuitka_cross.py: pack the OpenBSD standalone directory into Nuitka's
onefile bootstrap.

Nuitka's host onefile build leaves a *.onefile-build directory: the bootstrap's C sources,
onefile_definitions.h and blobs/__payload.bin, the packed host *.dist. The payload is
compiled in with #embed, so it can be replaced by one packed from the OpenBSD directory (by
Nuitka's own compressor, run with the host Python) before the bootstrap is cross-compiled.
The payload format holds file names, flags and sizes only: nothing in it depends on the host.

At run time the bootstrap extracts the payload to $TMPDIR (or /tmp) and runs the program from
there. The program keeps the python interpreter's PT_OPENBSD_WXNEEDED mark; outside a
wxallowed filesystem it still runs, only W|X mappings fail (ctypes callbacks).
"""
import json
import os
import re
import shlex
import subprocess

DEFINITIONS = "onefile_definitions.h"
PAYLOAD = os.path.join("blobs", "__payload.bin")
_DEFINE_RE = re.compile(r"^#define\s+(\w+)(?:\s+(.*))?$")

PACKER = """\
import json, sys
from nuitka.tools.onefile_compressor.OnefileCompressor import attachOnefilePayload
attachOnefilePayload(**json.loads(sys.argv[1]))
"""


class OnefileError(Exception):
    pass


def read_definitions(source_dir):
    """{macro: value} of the bootstrap's onefile_definitions.h."""
    path = os.path.join(source_dir, DEFINITIONS)
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        raise OnefileError(f"{path} missing: did the host onefile build run?") from None
    result = {}
    for line in lines:
        m = _DEFINE_RE.match(line.strip())
        if m:
            result[m.group(1)] = (m.group(2) or "").strip()
    return result


def set_payload_size(source_dir, size):
    path = os.path.join(source_dir, DEFINITIONS)
    with open(path) as f:
        text = f.read()
    text, n = re.subn(r"^#define _NUITKA_ONEFILE_PAYLOAD_SIZE_INT \d+$",
                      f"#define _NUITKA_ONEFILE_PAYLOAD_SIZE_INT {size}", text, flags=re.M)
    if n != 1:
        raise OnefileError(f"{path}: no _NUITKA_ONEFILE_PAYLOAD_SIZE_INT to update")
    with open(path, "w") as f:
        f.write(text)


def bootstrap_compile_lines(lines):
    """The C compile commands of the bootstrap, from Nuitka's --show-scons output (they come
    after the Onefile.scons scons command), and the sources they compile."""
    start = next((i for i, l in enumerate(lines) if "Onefile.scons" in l), None)
    if start is None:
        raise OnefileError("no Onefile.scons step in Nuitka's output (--show-scons)")
    compiles, sources = [], []
    for line in lines[start + 1:]:
        if " -c " not in line:
            continue
        try:
            argv = shlex.split(line)
        except ValueError:
            continue
        if argv and argv[-1].endswith(".c"):
            compiles.append(line)
            sources.append(argv[-1])
    if not sources:
        raise OnefileError("no bootstrap compile command in Nuitka's output (--show-scons)")
    return compiles, sources


def temp_mode(flags):
    """Does the bootstrap extract to a new temporary directory on each run (the default)?
    Nuitka passes it as a -D flag, not in onefile_definitions.h."""
    return any(f in ("-D_NUITKA_ONEFILE_TEMP_BOOL", "-D_NUITKA_ONEFILE_TEMP_BOOL=1")
               for f in flags)


def pack_payload(python, env, source_dir, dist_dir, main_exe, extract_to_temp, jobs):
    """Replace the bootstrap's payload with dist_dir packed the way the host build packed its
    own (compression, archive, checksums), and return its size."""
    defs = read_definitions(source_dir)
    if defs.get("_NUITKA_ONEFILE_HAS_PAYLOAD_BOOL") != "1":
        raise OnefileError("the host onefile build has no payload")
    # A main program outside the payload means a folder-mode build: not supported here.
    if "_NUITKA_ONEFILE_MAIN_FILENAME" in defs:
        raise OnefileError("onefile builds with the main program outside the payload are "
                           "not supported")
    payload = os.path.join(source_dir, PAYLOAD)
    os.unlink(payload)  # the packer appends
    args = {
        "dist_dir": dist_dir,
        "onefile_output_filename": payload,
        "start_binary": main_exe,
        "include_start_binary": True,
        "expect_compression": defs.get("_NUITKA_ONEFILE_COMPRESSION_BOOL") == "1",
        "as_archive": defs.get("_NUITKA_ONEFILE_ARCHIVE_BOOL") == "1",
        "use_compression_cache": False,
        # Checksums let a cached extraction be reused (Nuitka's rule).
        "file_checksums": not extract_to_temp,
        "win_path_sep": False,
        "low_memory": False,
        "job_limit": jobs,
        "expected_files": None,
    }
    r = subprocess.run([python, "-c", PACKER, json.dumps(args)], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise OnefileError(f"packing the onefile payload failed:\n{r.stderr.strip()}")
    size = os.path.getsize(payload)
    set_payload_size(source_dir, size)
    return size
