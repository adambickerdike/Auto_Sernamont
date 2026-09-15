"""Repository bootstrap: pin imports to ``pockels/`` and the CWD to the repo root.

Every entry script in this package imports this module first.  It does two
things, and the order matters:

1. **Pins ``sys.path[0]`` to ``pockels/``** so that sibling imports always
   resolve to this package.  When you run ``python pockels/x.py`` Python
   already does this, but Spyder's ``runfile`` executes the file inside the
   kernel *without* adding the script directory to ``sys.path``, so imports
   would then resolve through the working directory instead.

2. **Changes the working directory to the repository root.**  All run outputs
   (``pockels_fast_map/``, ``pockels_calibration/``, ``stage_calibration/``,
   ``calibration_results_*/`` ...) and all calibration discovery use
   CWD-relative paths, so every launch mode (CLI, Spyder, double-click, GUI
   child worker) must agree on where "here" is.

Net effect: data always lands in one predictable place, and code always
resolves to this package.  The function is idempotent and announces itself
once per process.
"""

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

# CRITICAL: put the package directory first on sys.path BEFORE changing
# directory, otherwise sibling imports would be resolved relative to the new
# working directory.
_package_str = str(PACKAGE_DIR)
while _package_str in sys.path:
    sys.path.remove(_package_str)
sys.path.insert(0, _package_str)

_moved = Path.cwd().resolve() != REPO_ROOT
if _moved:
    os.chdir(REPO_ROOT)

# Spyder/IPython autoreload may re-execute this module on every runfile; the
# path/cwd logic above is idempotent and the announce flag is stashed on sys
# so a module reload does not repeat it.
if _moved and not getattr(sys, "_pockels_bootstrap_announced", False):
    print(
        f"[pockels] Working directory pinned to {REPO_ROOT} "
        f"(runs and calibrations land here); imports pinned to {PACKAGE_DIR}"
    )
    sys._pockels_bootstrap_announced = True
