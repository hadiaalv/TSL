"""cPanel → Setup Python App → Execute python script: check_venv.py"""

from __future__ import annotations

import os
import sys
from glob import glob

print("executable:", sys.executable)
print("version:", sys.version)
print("cwd:", os.getcwd())
print("HOME:", os.environ.get("HOME"))

home = os.environ.get("HOME") or ""
patterns = [
    os.path.join(home, "virtualenv", "demo.summitstaffing.com.au", "*", "lib", "python*", "site-packages"),
    "/home2/summitst/virtualenv/demo.summitstaffing.com.au/*/lib/python*/site-packages",
]
print("site-packages candidates:")
for pat in patterns:
    for p in glob(pat):
        print(" -", p)

try:
    import a2wsgi

    print("a2wsgi: OK", getattr(a2wsgi, "__file__", ""))
except Exception as e:
    print("a2wsgi: FAIL", repr(e))

try:
    import fastapi

    print("fastapi: OK", fastapi.__version__)
except Exception as e:
    print("fastapi: FAIL", repr(e))
