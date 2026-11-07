"""白箱のテストが src/ccnavi/ を読む間だけ、src を sys.path に足す（tests/whitebox/）。"""

from tests import whitebox  # noqa: F401
