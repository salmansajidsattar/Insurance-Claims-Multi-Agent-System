"""Streamlit frontend. Talks only to the API.

Run:  streamlit run app/ui.py     (the API must be running: uvicorn app.api:app)
"""
import json
import os
import time
from datetime import date
from pathlib import Path

import httpx
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
ROOT = Path(__file__).resolve().parents[1]
ICONS = {"ok": "✅", "retry": "🔁", "failed": "❌", "skipped": "⏭️"}
VERDICT = {"auto_approve": ("Auto-approved", st.success), "human_review": ("Sent to human review", st.warning),
           "reject": ("Rejected", st.error), "processing": ("Processing...", st.info)}

st.set_page_config(page_title="Claims Harness", layout="wide")


def get(path: str):
    """GET from the API. Returns None on 404."""
    r = httpx.get(API + path, timeout=30)
    return None if r.status_code == 404 else r.json()


# ---------------------------------------------------------------- pieces
def show_package(policy_id: str) -> None:
    p = get(f"/policy/{policy_id}")
    if p is None:
        st.warning(f"Policy {policy_id} not found")
        return
    st.markdown(f"**Your package:** {p['product_name']} · {p['start']} to {p['end']}")
    st.dataframe([{"cover": c["peril"], "limit": f"${c['limit']:,.0f}", "deductible": f"${c['deductible']:,.0f}"}
                  for c in p["covers"]], hide_index=True)
    with st.expander("Not covered (exclusions) and conditions"):
        for c in p["exclusions"] + p["conditions"]:
            st.markdown(f"**{c['title']}** — {c['text']}")


def show_claim(claim_id: str) -> None:
    claim = get(f"/claim/{claim_id}")
    if claim is None:
        st.error(f"Claim {claim_id} not found")
        return
    s = claim["state"]
    st.markdown(f"### Claim `{claim_id}`")
    left, right = st.columns([1, 2])
    image = Path(s["submission"]["image_path"])
    image = image if image.is_absolute() else ROOT / image
    if image.exists():
        left.image(str(image), width="stretch")

    with right:
        label, box = VERDICT[claim["status"]]
        d = s["decision"]
        box(f"**{label}**" + (f" — {', '.join(d['reasons'])}" if d else ""))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Net payout", f"${s['payout']['net']:,.2f}" if s["payout"] else "—")
        c2.metric("Risk score", s["risk"]["score"] if s["risk"] else "—")
        c3.metric("Photo matches story", ("Yes" if s["damage"]["matches_story"] else "No") if s["damage"] else "—")
        c4.metric("Rules vs RAG", ("Agree" if s["rag"]["agrees"] else "Disagree") if s["rag"] else "—")
        if d and d["cited"]:
            st.caption("Clauses cited: " + ", ".join(d["cited"]))
        if s["rag"]:
            with st.expander("Policy wording used (RAG)"):
                for q in s["rag"]["quotes"]:
                    st.markdown(f"**{q['clause_id']}** — “{q['quote']}”")
        st.caption(s["submission"]["description"])

    st.subheader("Agent timeline")
    for e in get(f"/claim/{claim_id}/audit")["entries"]:
        with st.expander(f"{ICONS[e['status']]} {e['agent']} — {e['status']}"):
            st.write(e["note"])
            if e["error"]:
                st.error(e["error"])
            st.json({"input": e["input"], "output": e["output"]}, expanded=False)


# ---------------------------------------------------------------- pages
def page_submit() -> None:
    st.header("Submit a claim")
    samples = {c["claim_id"]: c["submission"] for c in json.loads((ROOT / "data/claims.json").read_text())}
    pick = st.selectbox("Pre-fill from a sample claim (optional)", ["—"] + list(samples))
    s = samples.get(pick, {})

    c1, c2 = st.columns(2)
    claimant = c1.text_input("Claimant ID", s.get("claimant_id", "C-001"), key=f"c_{pick}")
    policy_id = c2.text_input("Policy ID", s.get("policy_id", "POL-001"), key=f"p_{pick}")
    if policy_id:
        show_package(policy_id.strip())

    with st.form("claim"):
        incident = st.date_input("Incident date", date.fromisoformat(s["incident_date"]) if s else date.today(),
                                 max_value=date.today())
        estimate = st.number_input("Repair estimate total ($) — 0 if none", min_value=0.0, step=50.0)
        police = st.checkbox("Police report filed", s.get("police_report_filed", False))
        text = st.text_area("What happened? (max 100 words)", s.get("description", ""), height=130)
        upload = st.file_uploader("Damage photo", type=["jpg", "jpeg", "png"])
        sent = st.form_submit_button("Submit claim")

    words = len(text.split())
    st.caption(f"{words} / 100 words")
    if not sent:
        return
    if not 1 <= words <= 100:
        st.error(f"Description must be 1–100 words (now {words}).")
        return
    if upload:
        photo = (upload.name, upload.getvalue(), upload.type)
    elif s:
        photo = ("sample.jpg", (ROOT / s["image_path"]).read_bytes(), "image/jpeg")
    else:
        st.error("Please upload a photo.")
        return

    form = {"claimant_id": claimant, "policy_id": policy_id, "description": text,
            "incident_date": str(incident), "police_report_filed": str(police).lower()}
    if estimate > 0:
        form["repair_estimate_total"] = str(estimate)
    r = httpx.post(API + "/submit-claim", data=form, files={"image": photo}, timeout=60)
    if r.status_code != 202:
        st.error(f"API said {r.status_code}: {r.json().get('detail')}")
        return

    claim_id = r.json()["claim_id"]
    st.session_state["last"] = claim_id
    with st.spinner("Agents are working..."):
        for _ in range(300):                    # up to 10 minutes
            if get(f"/claim/{claim_id}")["status"] != "processing":
                break
            time.sleep(2)
    show_claim(claim_id)


def page_details() -> None:
    st.header("Claim details")
    claim_id = st.text_input("Claim ID", st.session_state.get("last", ""))
    if claim_id:
        show_claim(claim_id.strip())


def page_dashboard() -> None:
    st.header("All claims")
    rows = get("/claims")
    if not rows:
        st.info("No claims yet.")
        return
    cols = st.columns(3)
    for col, key in zip(cols, ["auto_approve", "human_review", "reject"]):
        col.metric(VERDICT[key][0], sum(r["status"] == key for r in rows))
    st.dataframe(rows, hide_index=True)
    pick = st.selectbox("Open a claim", [r["claim_id"] for r in rows])
    if pick:
        show_claim(pick)


# ---------------------------------------------------------------- main
st.sidebar.title("Claims Harness")
try:
    get("/health")
except httpx.HTTPError:
    st.error(f"API not reachable at {API}. Start it with: uvicorn app.api:app")
    st.stop()
pages = {"Submit claim": page_submit, "Claim details": page_details, "All claims": page_dashboard}
pages[st.sidebar.radio("Page", list(pages))]()
