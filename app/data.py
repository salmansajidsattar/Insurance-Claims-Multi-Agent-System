"""Reading and saving data. All files are plain JSON / Markdown in data/.

data/policies.json      customer packages (what is covered, limit, deductible)
data/policy_docs/*.md   company policy wording, one file per product
data/history.json       past claims per customer
data/estimates.json     repair estimates for the sample claims
data/claims.json        30 sample claims + expected answers (for tests only)
data/audit/<id>.json    saved result + audit trail of each processed claim
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from app import config
from app.schemas import ClaimState, Clause, Policy

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read_json(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


# ---- policy wording (Markdown) --------------------------------------------------
def parse_policy_doc(text: str) -> list[Clause]:
    """Each clause in the .md file looks like:

        ## COMP-EXC-DUI | Driving under the influence
        - type: exclusion
        - excludes: driver_impaired

        We will not pay for ...
    """
    clauses = []
    for section in text.split("\n## ")[1:]:
        header, *lines = section.strip().split("\n")
        clause_id, title = [x.strip() for x in header.split("|", 1)]
        meta, body = {}, []
        for line in lines:
            if line.startswith("- ") and ":" in line and not body:
                key, value = line[2:].split(":", 1)
                meta[key.strip()] = value.strip()
            elif line.strip():
                body.append(line.strip())
        clauses.append(Clause(
            id=clause_id, title=title, text=" ".join(body), type=meta["type"],
            perils=[p.strip() for p in meta.get("perils", "").split(",") if p.strip()],
            exclusion=meta.get("excludes"),
            window_days=int(meta["reporting_window_days"]) if "reporting_window_days" in meta else None,
        ))
    return clauses


@lru_cache(maxsize=None)
def product_clauses(product_id: str) -> tuple[Clause, ...]:
    text = (DATA / "policy_docs" / f"{product_id}.md").read_text(encoding="utf-8")
    return tuple(parse_policy_doc(text))


# ---- lookups --------------------------------------------------------------------
@lru_cache(maxsize=1)
def policies() -> dict[str, Policy]:
    out = {}
    for p in read_json("policies.json"):
        out[p["policy_id"]] = Policy(**p, clauses=list(product_clauses(p["product_id"])))
    return out


def get_policy(policy_id: str) -> Policy | None:
    return policies().get(policy_id)


def get_history(claimant_id: str) -> dict | None:
    return read_json("history.json").get(claimant_id)


def get_estimate_total(claim_id: str) -> float | None:
    """Sample claims: data/estimates.json. API claims: data/uploads/<id>_estimate.json."""
    estimates = read_json("estimates.json")
    if claim_id in estimates:
        return round(sum(item["cost"] for item in estimates[claim_id]["items"]), 2)
    uploaded = upload_dir() / f"{claim_id}_estimate.json"
    if uploaded.exists():
        return json.loads(uploaded.read_text())["total"]
    return None


def sample_claims() -> list[dict]:
    return read_json("claims.json")


def image_labels() -> dict:
    return json.loads((DATA / "images" / "manifest.json").read_text())["images"]


# ---- saving claims (audit trail) -------------------------------------------------
def audit_dir() -> Path:
    path = ROOT / os.getenv("AUDIT_DIR", config.AUDIT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def upload_dir() -> Path:
    path = ROOT / os.getenv("UPLOAD_DIR", config.UPLOAD_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _claim_file(claim_id: str) -> Path:
    if not claim_id.replace("-", "").isalnum():   # block "../" tricks
        raise ValueError(f"bad claim id: {claim_id}")
    return audit_dir() / f"{claim_id}.json"


def save_state(state: ClaimState) -> None:
    path = _claim_file(state.claim_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)   # write then rename: never a half-written file


def load_state(claim_id: str) -> ClaimState | None:
    path = _claim_file(claim_id)
    if not path.exists():
        return None
    return ClaimState.model_validate_json(path.read_text(encoding="utf-8"))


def list_states() -> list[ClaimState]:
    states = [ClaimState.model_validate_json(p.read_text(encoding="utf-8"))
              for p in audit_dir().glob("*.json")]
    return sorted(states, key=lambda s: s.submission.submitted_at, reverse=True)
