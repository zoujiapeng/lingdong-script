"""Pytest bootstrap for the WinPilot package.

winpilot_home is not itself a package (the package is ``winpilot`` inside it),
so put winpilot_home on sys.path to make ``import winpilot...`` resolve no
matter where pytest is invoked from. Mirrors what agent/__main__.py does at
runtime.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
