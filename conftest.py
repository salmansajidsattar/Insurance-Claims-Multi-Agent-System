"""pytest setup: every test saves claims/uploads in a temp folder, and uses fake models."""
import pytest

from tests.fakes import fakes_off, fakes_on


@pytest.fixture(autouse=True)
def temp_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))


@pytest.fixture(autouse=True)
def fake_models():
    fakes_on()
    yield
    fakes_off()
