# passenger_wsgi.py — cPanel / Passenger entry for online ERP host
import os
import sys

# App root (folder that contains run.py and app/)
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# Optional: host virtualenv site-packages (uncomment and fix path if needed)
# VENV = os.path.join(BASE, "virtualenv", "lib", "python3.11", "site-packages")
# if os.path.isdir(VENV) and VENV not in sys.path:
#     sys.path.insert(0, VENV)

from dotenv import load_dotenv

load_dotenv(os.path.join(BASE, ".env"))

# Force online mode if host forgot APP_MODE in panel env
os.environ.setdefault("APP_MODE", "online")
os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("OFFLINE_FIRST", "false")
os.environ.setdefault("DATABASE_MODE", "production")
os.environ.setdefault("SYNC_AUTO_ENABLED", "false")
os.environ.setdefault("AUTO_BACKUP_ENABLED", "false")

from run import app as application  # noqa: E402
