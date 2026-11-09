"""textkit - a tiny text utility library.

This package is the sample benchmark repository shipped with AgentBench Lab. It
is written from scratch for this project (MIT licensed) and contains deliberate
defects and gaps that the bundled benchmark tasks ask an agent to fix.
"""

from textkit.slug import slugify
from textkit.truncate import truncate
from textkit.wrap import wrap_text

__all__ = ["slugify", "truncate", "wrap_text"]
__version__ = "0.1.0"
