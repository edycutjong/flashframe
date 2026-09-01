import os
import sys

# Add the repository root to sys.path so we can import web.py
# __file__ is tests/conftest.py, os.path.dirname is tests/, os.path.dirname of that is repo root.
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
