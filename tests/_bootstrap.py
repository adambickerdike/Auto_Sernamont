"""Make the measurement package importable from the tests directory.

The tests deliberately import no instrument drivers: they parse the large
modules with ``ast`` and exercise individual functions in isolation, or they
import the pure-analysis modules directly.  That is what lets the whole suite
run on any machine, without PyVISA, pythonnet or a display.

``module_path(name)`` returns the path of a module inside ``pockels/`` so a
test can read its source without importing it.
"""

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "pockels"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


def module_path(name):
    """Return the path of ``name`` inside the measurement package."""
    return PACKAGE_DIR / name
