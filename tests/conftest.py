import pytest

from app.pdfs import MOCK_EXTRACTIONS
from app.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(db_path=tmp_path / "test.db", outbox_dir=tmp_path / "outbox",
                extractions_dir=MOCK_EXTRACTIONS)
    s.seed()
    yield s
    s.store.close()
