"""cPanel / LiteSpeed Passenger entry — no schema migration here.

Schema: run once via  python scripts/migrate_schema.py
Then set env (Setup Python App) and Stop/Start the app:
  LSAPI_CHILDREN=3
  LSAPI_MAX_IDLE=120
  LSAPI_MAX_IDLE_CHILDREN=3
  LSAPI_PGRP_MAX_IDLE=300
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from portal import create_app

application = create_app()
