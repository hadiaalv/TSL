"""
cPanel Passenger entry. MUST load cos_wsgi.py — never load passenger_wsgi.py itself
(that causes RecursionError / timeout).
"""

import importlib.machinery
import importlib.util
import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)


def load_source(modname, filename):
    path = filename if os.path.isabs(filename) else os.path.join(APP_ROOT, filename)
    loader = importlib.machinery.SourceFileLoader(modname, path)
    spec = importlib.util.spec_from_file_location(modname, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# IMPORTANT: cos_wsgi.py — NOT passenger_wsgi.py
wsgi = load_source("cos_wsgi", "cos_wsgi.py")
application = wsgi.application
