"""Make `python3 -m unittest discover` from the repo root work; expose app/ on sys.path."""
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(REPO_ROOT, "app")
for _p in (APP_DIR, os.path.join(APP_DIR, "guest"), os.path.join(APP_DIR, "phase2")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
