# Code walkthrough — follow one claim through the code

The whole app is 10 small files in `app/` (~1,250 lines). Read them in this order.

```
app/
  config.py     1. settings (model names, limits, risk weights)
  schemas.py    2. the data shapes passed between agents
  data.py       3. read policies / history / estimates, save each claim
  llm.py        4. call Ollama (chat + embeddings)
  rag.py        5. search the policy wording (BM25 + embeddings)
  agents.py     6. the 6 agents
  router.py     7. approve / review / reject
  pipeline.py   8. LangGraph: run the agents in order
  api.py        9. FastAPI endpoints
  ui.py        10. Streamlit screens
tests/
  fakes.py         fake models so tests run without Ollama
  test_pipeline.py agents, router, pipeline, all 30 sample claims
  test_api.py      API + UI
  test_live.py     3 claims with the real models (RUN_LIVE=1)
  eval.py          pass rate on the 30 sample claims
data/
  policies.json      customer packages: cover, limit, deductible
  policy_docs/*.md   company policy wording (what RAG searches)
  history.json       past claims per customer
  estimates.json     repair costs for the sample claims
  claims.json        30 sample claims + the right answers
  images/            car damage photos + labels
  audit/             one JSON file per processed claim (created at run time)
```

## The example claim

`CLM-EVAL-001`: customer C-001, policy POL-001, photo `img_rear_bumper_dent_01.jpg`,
"The car behind me hit my rear bumper at a red light... Police report filed."

---

## Step 0 — the claim comes in (`api.py`)

The UI sends a form + photo to `POST /submit-claim`. The API:

1. checks the policy exists and belongs to this customer (else 404 / 422)
2. checks the photo is JPEG/PNG and under 10 MB (else 415 / 413)
3. builds a `Submission` — this checks the description is 1–100 words (else 422)
4. saves the repair estimate if given
5. saves the claim as "processing" and starts `process_claim()` in the background
6. returns `{"claim_id": ..., "status": "processing"}` straight away

## Step 1 — the pipeline starts (`pipeline.py`)

`process_claim()` creates a `ClaimState` (see `schemas.py`). It has one empty slot per agent:

```
claim  damage  policy  rag  risk  payout  decision   + audit list + failures list
```

LangGraph runs the steps in this fixed order:
`intake → damage → policy → policy_rag → risk → payout → router`.
After each step `as_node()` saves the whole state to `data/audit/<claim_id>.json`.
If anything crashes, the `except` block sends the claim to human review.

## How every agent is protected (`agents.py → run_agent`)

Each agent puts its work in a small function `work()` and calls `run_agent()`:

- try `work()` → if OK, add an audit entry `status="ok"` and return the result
- if it fails (bad JSON, missing field, invented clause, Ollama down…) → audit `retry`, try again
- fails again → audit `failed`, add the agent to `state.failures` → router sends to human review

The results are Pydantic models with `extra="forbid"`, so a model that returns
unexpected fields fails validation.

## Step 2 — Intake agent (`agents.intake`) · LLM

Sends the description to `qwen2.5:7b` with `INTAKE_PROMPT` and gets back 5 facts:

```json
{"incident_type": "collision", "zones": ["rear"], "severity": "moderate",
 "third_party": true, "circumstances": []}
```

`IntakeAnswer` checks the values are allowed ones. The claim id, dates and police
report are copied from the form in code — the LLM never touches them. Result → `state.claim`.

## Step 3 — Damage agent (`agents.damage`) · vision LLM + rules

Sends the photo to `qwen2.5vl:7b`, which only **describes** it, e.g.
`{"damage_type": "dent", "severity": "moderate", "zones": ["rear"], "confidence": 0.9, "image_usable": true}`.

Then plain code (`compare_photo_to_story`) decides if the photo fits the story:

- photo unusable → `image_unusable`
- claimed area vs photo area don't overlap (front / rear / side...) → `wrong_area`
- severity 2+ levels apart (e.g. "minor" vs "severe") → `wrong_severity`
- damage type doesn't fit the incident (e.g. crush for a flood) → `wrong_damage_type`

For our claim: rear vs rear, moderate vs moderate → `matches_story = True`.

## Step 4 — Policy agent (`agents.policy`) · plain rules

`check_policy()` looks at the customer's package (`policies.json`) and the wording:

a. incident date inside the policy period? else `policy_inactive`
b. is the peril in the package? else `peril_not_covered`
   (if yes, take that cover's **limit** and **deductible**)
c. does an exclusion match a circumstance (e.g. `driver_impaired`)? → `exclusion_hit`
d. reported within the window (e.g. 30 days)? else `condition_failed`

Every rule it used goes into `cited` (clause ids like `COMP_PLUS-COV-COLLISION`,
package line `POL-001-SCH-COLLISION`). The agent then checks every cited id really
exists in the policy — it can never invent a rule.

Our claim: in period, collision is covered (limit 25,000, deductible 500), no exclusion,
reported after 1 day → `covered = True`.

## Step 5 — Policy RAG agent (`agents.policy_rag`) · search + LLM

A second, independent opinion from the policy **text**:

1. `rag_query()` builds a search sentence from the facts (not from the user's words)
2. `rag.search()` ranks the product's clauses two ways — keyword score (BM25) and
   meaning score (embeddings from `nomic-embed-text`) — merges the two rankings and keeps the top 6
3. the LLM reads only those 6 clauses + the package and answers covered / exclusions /
   cited / quotes
4. `check_rag_answer()` rejects any clause that wasn't retrieved and any quote that isn't
   copied word-for-word
5. `agrees = True` only if RAG and the rules give the same covered answer and exclusions

If they disagree, the router sends the claim to a person.

## Step 6 — Risk agent (`agents.risk`) · plain rules

Adds up weights from `config.RISK_WEIGHTS` for each flag that applies: 3+ claims in a
year, past fraud flag, photo mismatch, low photo confidence, reported after 14 days,
new policy (< 30 days), other party but no police report. Score is capped at 1.0.
Our claim: no flags → `score = 0.0`.

## Step 7 — Payout agent (`agents.payout`) · maths

Only if covered. `make_payout()`:

```
eligible = min(estimate, limit)        min(930, 25000) = 930
deductible = min(deductible, eligible) 500
net = eligible - deductible            430
```

## Step 8 — Router (`router.py`) · plain if-statements

1. **Human review** if any of: an agent failed, data missing, photo doesn't match,
   photo confidence < 0.7, rules and RAG disagree, risk ≥ 0.6, net payout > $5,000
2. else **reject** if not covered (with the policy's reasons)
3. else **auto-approve**

Our claim → `auto_approve`, reasons `["all_checks_passed"]`, net $430.

## Step 9 — reading the result (`api.py` + `ui.py`)

The UI polls `GET /claim/{id}` every 2 s until the status is not "processing", then shows
the verdict, payout, risk, photo check, rules-vs-RAG, quoted wording, and the agent
timeline from `GET /claim/{id}/audit` (one row per audit entry).

---

## Tests and eval

- `tests/fakes.py` replaces the models with answers from `data/claims.json` and the photo
  labels, so `pytest` runs in seconds without Ollama.
- `tests/test_pipeline.py` runs all 30 sample claims and checks the outcome, reasons,
  payout, citations, RAG agreement and audit order, plus every router rule and failure case.
- `python -m tests.eval` runs the 30 claims with the **real** models and writes the pass rate
  to `reports/`.

## What to change where

| I want to… | Edit |
|---|---|
| change limits (risk 0.6, payout $5,000, confidence 0.7) | `.env` |
| change risk weights | `app/config.py` → `RISK_WEIGHTS` |
| improve what the LLM extracts | `app/agents.py` → `INTAKE_PROMPT`, `PHOTO_PROMPT`, `RAG_PROMPT` |
| change photo-vs-story rules | `app/agents.py` → `compare_photo_to_story` |
| change a customer's package | `data/policies.json` |
| change policy wording | `data/policy_docs/<PRODUCT>.md` |
| add a test claim | `data/claims.json` (+ estimate in `data/estimates.json`) |
