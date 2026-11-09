import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calc import add


def test_add_two_positives():
    assert add(2, 3) == 5


def test_add_negatives():
    assert add(-1, -1) == -2
