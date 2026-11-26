import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIREMENTS_MD = ROOT / "docs" / "requirements.md"
SAMPLES = ROOT / "samples"


@pytest.fixture(scope="session")
def requirements_doc():
    from reqcheck.ingest import load_document

    return load_document(REQUIREMENTS_MD)
