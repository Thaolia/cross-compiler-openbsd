"""Local module: compiled into the program because nuitka_cross.py follows imports."""
WORDS = ["puffy", "blowfish", "openbsd", "nuitka"]


def is_compiled():
    return "__compiled__" in globals()


def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)
