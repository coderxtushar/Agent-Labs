"""Make ``src/`` importable without an install step.

Benchmark runs happen in a network-disabled sandbox, so the test suite must not
depend on ``pip install``.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
