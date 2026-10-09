"""Puts vcode-bridge's directory on the import path, so the tests import manage.py where it lives.

Author: Marco Cassar (@Ocramaru)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
