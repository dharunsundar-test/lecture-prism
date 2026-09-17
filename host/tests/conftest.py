import os
import tempfile

# Settings are read at import time, so point them at a throwaway data dir before the app loads.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="lecture-capture-test-")
os.environ["WORKER_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from app.core.pairing import get_pair_token
from app.main import app


@pytest.fixture(scope="session")
def lan_client():
    """Requests from TestClient's default client address count as coming over the LAN."""
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def local_client():
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client


@pytest.fixture
def phone_headers():
    return {"X-Pair-Token": get_pair_token()}
