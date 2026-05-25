"""Pytest fixtures shared across all tests."""
import sys
from pathlib import Path

# Ensure the project root is on sys.path so that "from app.services... import" works.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
