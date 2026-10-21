"""Real WSGI app for cPanel Passenger. Loaded by passenger_wsgi.py."""

from __future__ import annotations

import os
import sys
from glob import glob

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ.setdefault("DATA_DIR", os.path.join(APP_ROOT, "data"))
os.makedirs(os.path.join(APP_ROOT, "data"), exist_ok=True)
os.makedirs(os.path.join(APP_ROOT, "tmp"), exist_ok=True)

HOME = os.environ.get("HOME") or "/home2/summitst"
for pattern in (
    os.path.join(HOME, "virtualenv", "demo.summitstaffing.com.au", "*", "lib", "python*", "site-packages"),
    os.path.join(HOME, "virtualenv", "demo.summitstaffing.com.au", "*", "lib64", "python*", "site-packages"),
):
    for site in sorted(glob(pattern), reverse=True):
        if site not in sys.path:
            sys.path.insert(0, site)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(APP_ROOT, ".env"), override=False)
except Exception:
    pass

from a2wsgi import ASGIMiddleware
from app.main import app as asgi_app

application = ASGIMiddleware(asgi_app)
