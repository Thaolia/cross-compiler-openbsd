"""Nuitka user plugin used by nuitka_cross.py (--user-plugin=<this file>).

An accelerated-mode program stores, as constants, the sys.executable and sys.prefix values of
the interpreter that ran Nuitka, and restores them at startup. Built on Linux for OpenBSD, those
would be build-host paths; this plugin substitutes the OpenBSD interpreter's values while the
constants are generated, and nowhere else (Nuitka itself keeps using the host's paths).

Target values come from the environment set by nuitka_cross.py:
    OPENBSD_NUITKA_EXECUTABLE   e.g. /usr/local/bin/python3.14
    OPENBSD_NUITKA_PREFIX       e.g. /usr/local
"""
import functools
import os
import sys

from nuitka.plugins.PluginBase import NuitkaPluginBase

_SYS_PATH_ATTRS = ("prefix", "exec_prefix", "base_prefix", "base_exec_prefix")


def _with_target_sys_paths(func, executable, prefix):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        saved = {a: getattr(sys, a) for a in ("executable",) + _SYS_PATH_ATTRS}
        sys.executable = executable
        for a in _SYS_PATH_ATTRS:
            setattr(sys, a, prefix)
        try:
            return func(*args, **kwargs)
        finally:
            for a, v in saved.items():
                setattr(sys, a, v)
    return wrapper


class OpenBSDTargetPlugin(NuitkaPluginBase):
    plugin_name = __file__
    plugin_desc = "Use the OpenBSD interpreter paths in the generated program."

    def __init__(self):
        executable = os.environ.get("OPENBSD_NUITKA_EXECUTABLE")
        prefix = os.environ.get("OPENBSD_NUITKA_PREFIX")
        if not executable or not prefix:
            self.sysexit("OPENBSD_NUITKA_EXECUTABLE and OPENBSD_NUITKA_PREFIX must be set "
                         "(run through nuitka_cross.py)")
        # Imported here: the plugin file is loaded before Nuitka's options are parsed, and
        # code generation modules need them. Patch the name CodeGeneration calls, which it
        # imported from ConstantCodes.
        from nuitka.code_generation import CodeGeneration

        original = getattr(CodeGeneration, "getConstantsDefinitionCode", None)
        if original is None:
            self.sysexit("this Nuitka version has no CodeGeneration.getConstantsDefinitionCode; "
                         "nuitka_cross.py supports the version pinned in NUITKA_VERSION")
        CodeGeneration.getConstantsDefinitionCode = _with_target_sys_paths(
            original, executable, prefix)
