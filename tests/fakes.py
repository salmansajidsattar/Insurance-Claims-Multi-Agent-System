"""Fake models for tests: answers come from the known right answers, so no Ollama is needed.

- intake: returns the expected facts of the sample claim
- photo:  returns the labels in data/images/manifest.json
- RAG:    a 'perfect reader' — but it can only cite/quote clauses that were retrieved,
          so a retrieval miss still shows up as a failure
- embed:  a simple bag-of-words vector
"""
import hashlib
import re
from functools import lru_cache
from pathlib import Path

from app import data, llm, rag

SAMPLES = {c["claim_id"]: c for c in data.sample_claims()}


@lru_cache(maxsize=1)
def labels_by_hash() -> dict:
    folder = data.DATA / "images"
    return {hashlib.md5((folder / name).read_bytes()).hexdigest(): label
            for name, label in data.image_labels().items()}


def fake_photo(image_path: str) -> dict:
    label = labels_by_hash()[hashlib.md5(Path(image_path).read_bytes()).hexdigest()]
    return {"image_usable": label["image_usable"], "damage_type": label["damage_type"],
            "severity": label["severity"], "zones": label["accepted_zones"],
            "confidence": 0.9 if label["image_usable"] else 0.2}


def fake_intake(user: str) -> dict:
    for c in SAMPLES.values():
        if c["submission"]["description"] in user:
            e = c["expected"]
            return {"incident_type": e["incident_type"], "zones": e["zones"], "severity": e["severity"],
                    "third_party": e["third_party"], "circumstances": e["circumstances"]}
    raise AssertionError("unknown description")


def fake_rag(user: str) -> dict:
    claim_id = re.search(r"Claim reference: (\S+)", user).group(1)
    clauses = dict(re.findall(r"^\[([A-Z_]+-[A-Z_-]+)\] [^:]+: (.+)$", user, re.M))
    package = re.findall(r"^- \[(POL-[\w-]+)\] (\w+):", user, re.M)

    if claim_id in SAMPLES:                       # sample claim: use the right answer
        e = SAMPLES[claim_id]["expected"]
        covered, incident, circumstances = e["covered"], e["incident_type"], e["circumstances"]
        pol = data.get_policy(SAMPLES[claim_id]["submission"]["policy_id"])
    else:                                         # API claim: copy the saved rules result
        s = data.load_state(claim_id)
        covered, incident, circumstances = s.policy.covered, s.claim.incident_type, s.claim.circumstances
        pol = data.get_policy(s.submission.policy_id)

    exclusions = [cid for cid in clauses if pol.clause(cid).exclusion in circumstances]
    cited = [cid for cid in clauses if incident in pol.clause(cid).perils] + exclusions
    cited += [pid for pid, peril in package if peril == incident]
    quote_ids = [c for c in cited if c in clauses] or list(clauses)[:1]
    return {"covered": covered, "exclusions": exclusions, "cited": cited or quote_ids,
            "quotes": [{"clause_id": c, "quote": clauses[c].split(". ")[0]} for c in quote_ids]}


def fake_ask(system: str, user: str, image_path: str | None = None) -> dict:
    if "claims analyst" in system:
        return fake_rag(user)
    if image_path:
        return fake_photo(image_path)
    return fake_intake(user)


def fake_embed(texts: list[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        v = [0.0] * 256
        for w in rag.words(text):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
        vectors.append(v)
    return vectors


def fakes_on() -> None:
    llm.use_fake(ask=fake_ask, embed=fake_embed)
    rag.clear_cache()


def fakes_off() -> None:
    llm.use_fake()
    rag.clear_cache()
