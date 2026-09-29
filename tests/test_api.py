"""Tests for the FastAPI backend and the Streamlit UI (fake models)."""
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import data
from app.api import app
from tests.fakes import SAMPLES

client = TestClient(app)


def submit(claim_id: str, **changes):
    """Send a sample claim to the API the way the UI does."""
    s = SAMPLES[claim_id]["submission"]
    form = {"claimant_id": s["claimant_id"], "policy_id": s["policy_id"], "description": s["description"],
            "incident_date": str(date.today() - timedelta(days=2)),
            "police_report_filed": str(s["police_report_filed"]).lower(),
            "repair_estimate_total": str(data.get_estimate_total(claim_id))}
    form.update(changes)
    photo = (data.ROOT / s["image_path"]).read_bytes()
    return client.post("/submit-claim", data=form, files={"image": ("photo.jpg", photo, "image/jpeg")})


@pytest.mark.parametrize("claim_id,outcome", [("CLM-EVAL-001", "auto_approve"),
                                              ("CLM-EVAL-013", "human_review"),
                                              ("CLM-EVAL-017", "reject")])
def test_submit_and_read_back(claim_id, outcome):
    r = submit(claim_id)
    assert r.status_code == 202
    new_id = r.json()["claim_id"]
    assert client.get(f"/claim/{new_id}").json()["status"] == outcome
    agents = [e["agent"] for e in client.get(f"/claim/{new_id}/audit").json()["entries"]]
    assert agents == ["intake", "damage", "policy", "policy_rag", "risk", "payout", "router"]
    assert new_id in {c["claim_id"] for c in client.get("/claims").json()}


def test_no_estimate_goes_to_review():
    new_id = submit("CLM-EVAL-001", repair_estimate_total="").json()["claim_id"]
    assert client.get(f"/claim/{new_id}").json()["status"] == "human_review"


@pytest.mark.parametrize("change,code", [({"description": "word " * 101}, 422),
                                         ({"incident_date": "2099-01-01"}, 422),
                                         ({"policy_id": "POL-999"}, 404),
                                         ({"claimant_id": "C-002"}, 422)])
def test_bad_input_is_refused(change, code):
    assert submit("CLM-EVAL-001", **change).status_code == code


def test_wrong_file_type():
    r = client.post("/submit-claim", files={"image": ("a.txt", b"hi", "text/plain")},
                    data={"claimant_id": "C-001", "policy_id": "POL-001", "description": "hit a wall",
                          "incident_date": "2026-09-01"})
    assert r.status_code == 415


def test_policy_package():
    p = client.get("/policy/POL-003").json()
    assert {c["peril"]: (c["limit"], c["deductible"]) for c in p["covers"]}["collision"] == (15000, 250)
    assert client.get("/policy/POL-999").status_code == 404


def test_unknown_claim():
    assert client.get("/claim/CLM-NOPE").status_code == 404


# ---------------------------------------------------------------- Streamlit UI
st_testing = pytest.importorskip("streamlit.testing.v1")


@pytest.fixture
def ui(monkeypatch):
    """Run the UI with its API calls sent straight to the FastAPI test client."""
    base = "http://localhost:8000"
    monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url.replace(base, "")))
    monkeypatch.setattr(httpx, "post", lambda url, data=None, files=None, **kw:
                        client.post(url.replace(base, ""), data=data, files=files))

    def open_page(name):
        at = st_testing.AppTest.from_file(str(data.ROOT / "app/ui.py"), default_timeout=60)
        at.run()
        at.sidebar.radio[0].set_value(name).run()
        return at
    return open_page


def test_ui_submit_sample(ui):
    at = ui("Submit claim")
    at.selectbox[0].set_value("CLM-EVAL-017").run()      # drunk driving -> reject
    assert any("Comprehensive" in m.value or "Premium" in m.value for m in at.markdown)  # package shown
    at.button[0].click().run()
    assert not at.exception
    assert any("Rejected" in e.value for e in at.error)


def test_ui_word_limit(ui):
    at = ui("Submit claim")
    at.text_area[0].set_value("word " * 101).run()
    at.button[0].click().run()
    assert any("1–100 words" in e.value for e in at.error)


def test_ui_other_pages(ui):
    assert not ui("All claims").exception
    at = ui("Claim details")
    at.text_input[0].set_value("CLM-NOPE").run()
    assert any("not found" in e.value for e in at.error)
