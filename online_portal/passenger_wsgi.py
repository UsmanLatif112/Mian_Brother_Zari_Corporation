"""cPanel Passenger entry point — upload this folder as a new app."""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from portal import create_app

application = create_app()
