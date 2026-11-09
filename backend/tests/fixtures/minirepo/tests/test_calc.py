import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calc import add, mul


def test_add_identity_still_works():
    assert add(2, 0) == 2


def test_mul():
    assert mul(3, 4) == 12
