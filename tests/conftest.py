"""
Shared pytest setup.

Two things in this repo assume they run from the repo root:
  - mcp_server.py loads data/index/chunks.json relative to the current
    working directory.
  - backend/app/auth.py reads AWS_REGION / COGNITO_POOL_ID /
    COGNITO_CLIENT_ID from the environment at import time.

This file makes both true regardless of where `pytest` is invoked from, and
puts safe, obviously-fake Cognito settings in place *before* anything can
import auth.py, so the test suite never needs a real Cognito user pool and
never risks reading real developer .env values.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("COGNITO_POOL_ID", "us-east-1_TESTPOOL")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client-id")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)
