"""FastAPI backend.   Run:  uvicorn app.api:app --reload    Docs: http://localhost:8000/docs"""
import json
from datetime import date

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from app import data
from app.pipeline import new_claim_id, process_claim
from app.schemas import ClaimState, Submission

app = FastAPI(title="Insurance Claims Harness")
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/submit-claim", status_code=202)
async def submit_claim(
    background: BackgroundTasks,
    image: UploadFile = File(...),
    claimant_id: str = Form(...),
    policy_id: str = Form(...),
    description: str = Form(...),
    incident_date: date = Form(...),
    police_report_filed: bool = Form(False),
    repair_estimate_total: float | None = Form(None),
):
    # 1. check the policy
    policy = data.get_policy(policy_id)
    if policy is None:
        raise HTTPException(404, f"policy {policy_id} not found")
    if policy.holder_id != claimant_id:
        raise HTTPException(422, "this policy belongs to someone else")

    # 2. check and save the photo
    if image.content_type not in IMAGE_TYPES:
        raise HTTPException(415, "photo must be JPEG or PNG")
    photo = await image.read()
    if not photo or len(photo) > 10 * 1024 * 1024:
        raise HTTPException(413, "photo is empty or bigger than 10 MB")
    claim_id = new_claim_id()
    photo_file = data.upload_dir() / f"{claim_id}{IMAGE_TYPES[image.content_type]}"
    photo_file.write_bytes(photo)

    # 3. check the form (100 words, date not in the future)
    try:
        submission = Submission(claimant_id=claimant_id, policy_id=policy_id, description=description,
                                incident_date=incident_date, image_path=str(photo_file),
                                police_report_filed=police_report_filed)
    except ValidationError as e:
        raise HTTPException(422, [err["msg"] for err in e.errors()])
    if incident_date > submission.submitted_at.date():
        raise HTTPException(422, "incident date is in the future")

    # 4. save the repair estimate (without it the claim goes to review)
    if repair_estimate_total:
        (data.upload_dir() / f"{claim_id}_estimate.json").write_text(
            json.dumps({"total": repair_estimate_total}))

    # 5. save as "processing" and run the agents in the background
    data.save_state(ClaimState(claim_id=claim_id, submission=submission))
    background.add_task(process_claim, submission, claim_id)
    return {"claim_id": claim_id, "status": "processing"}


def load_or_404(claim_id: str) -> ClaimState:
    try:
        state = data.load_state(claim_id)
    except ValueError:
        state = None
    if state is None:
        raise HTTPException(404, f"claim {claim_id} not found")
    return state


def status(state: ClaimState) -> str:
    return state.decision.outcome if state.decision else "processing"


@app.get("/claim/{claim_id}")
def get_claim(claim_id: str):
    state = load_or_404(claim_id)
    return {"claim_id": claim_id, "status": status(state), "state": state.model_dump(mode="json")}


@app.get("/claim/{claim_id}/audit")
def get_audit(claim_id: str):
    state = load_or_404(claim_id)
    return {"claim_id": claim_id, "status": status(state),
            "entries": [e.model_dump(mode="json") for e in state.audit]}


@app.get("/claims")
def list_claims():
    return [{"claim_id": s.claim_id, "status": status(s), "claimant_id": s.submission.claimant_id,
             "policy_id": s.submission.policy_id, "net_payout": s.payout.net if s.payout else None,
             "reasons": s.decision.reasons if s.decision else [],
             "submitted_at": s.submission.submitted_at.isoformat()} for s in data.list_states()]


@app.get("/policy/{policy_id}")
def get_policy(policy_id: str):
    policy = data.get_policy(policy_id)
    if policy is None:
        raise HTTPException(404, f"policy {policy_id} not found")
    return {"policy_id": policy.policy_id, "product_name": policy.product_name,
            "start": str(policy.start), "end": str(policy.end),
            "covers": [c.model_dump() for c in policy.covers],
            "exclusions": [{"title": c.title, "text": c.text} for c in policy.clauses if c.type == "exclusion"],
            "conditions": [{"title": c.title, "text": c.text} for c in policy.clauses if c.type == "condition"]}
