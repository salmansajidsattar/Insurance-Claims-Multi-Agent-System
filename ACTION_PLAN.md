# Action Plan — Insurance Claims Multi-Agent Harness

Paste this into Claude Cowork as the working plan. Work phase by phase — don't skip ahead.
CLAUDE.md holds the hard architecture rules; this file is the sequence of tasks.

## Phase 0 — Project setup
- [ ] Create repo with the folder structure defined in CLAUDE.md
- [ ] `requirements.txt`: fastapi, uvicorn, streamlit, langgraph, pydantic>=2, openai (or your
      VLM client), python-dotenv, pytest, httpx
- [ ] `.env` for API keys (VLM provider key, etc.) — never commit this
- [ ] Place CLAUDE.md at repo root

## Phase 1 — Schemas first
- [ ] Define every Pydantic model in `/backend/schemas`: `NormalizedClaim`, `DamageAssessment`,
      `PolicyEvaluation`, `RiskAssessment`, `PayoutDecision`, `AuditLogEntry`
- [ ] Define `ClaimState` in `/backend/graph/state.py` — the single object that flows through the
      whole LangGraph, holding all of the above as it's progressively filled in
- [ ] Write these before any agent code — every agent's job is to fill one part of `ClaimState`

## Phase 2 — Synthetic data
- [ ] Pull a small set of vehicle damage images (Kaggle car damage dataset works)
- [ ] Generate `policies.json`: 5–10 policies with realistic coverage limits, deductibles, and at
      least 2–3 explicit exclusions each (e.g. intentional damage, pre-existing damage)
- [ ] Generate `repair_estimates.json` and `claim_history.json` (a few claimants with prior claims,
      some with red flags like multiple recent claims)
- [ ] Generate `claims.json`: 15–20 synthetic claims spanning the 3 outcomes (clear approve, clear
      reject, ambiguous/needs-review) — this becomes your eval set later

## Phase 3 — Agents (build and unit-test one at a time)
- [ ] `IntakeAgent` — parses raw description + metadata into `NormalizedClaim`
- [ ] `DamageEvidenceAgent` — VLM call on the image, cross-checks against the narrative, sets
      `narrative_match` and `confidence`
- [ ] `PolicyAgent` — loads the claimant's policy, checks coverage/deductible/exclusions, must
      populate `cited_clauses` for every decision (no hallucinated rules — see CLAUDE.md rule 3)
- [ ] `RiskAgent` — combines claim history + damage/narrative mismatch + timing to produce
      `risk_score` and `flags`
- [ ] `PayoutAgent` — computes gross/net payout after deductible, only runs if PolicyAgent said
      covered
- [ ] For each agent: write it, then write its 3 eval cases (approve/review/reject) before moving on

## Phase 4 — LangGraph orchestration
- [ ] Build `build_graph.py`: wire the 5 agents into a `StateGraph` in sequence:
      Intake → DamageEvidence → Policy → Risk → Payout → Router
- [ ] Build `router.py`: explicit conditional logic implementing the 3 outcomes
      (auto-approve / human-review / reject) per CLAUDE.md rules 4 and 5 — no LLM judgment call here
- [ ] Confirm the full graph runs end-to-end on 2–3 synthetic claims and produces a `ClaimState`
      with every field populated plus an audit trail

## Phase 5 — Audit logging
- [ ] Every agent writes an `AuditLogEntry` (input, output, reasoning, timestamp) to the claim's
      audit trail as it runs — not just the final decision
- [ ] Persist audit trail per `claim_id` (SQLite table or JSON file per claim)

## Phase 6 — FastAPI backend
- [ ] `POST /submit-claim` — accepts image + description, kicks off the graph, returns `claim_id`
- [ ] `GET /claim/{claim_id}` — returns current `ClaimState` and final decision
- [ ] `GET /claim/{claim_id}/audit` — returns the full audit trail / agent timeline
- [ ] `GET /claims` — list all claims (for the dashboard)

## Phase 7 — Streamlit frontend
- [ ] Upload form: image + text description (enforce ≤100 words client-side)
- [ ] Submit → poll or call `/claim/{id}` → show verdict (approve/review/reject) with reasoning
- [ ] Agent timeline view: show each agent's output for the claim in order (pulls from the audit
      endpoint)
- [ ] Simple claims list/dashboard view

## Phase 8 — Evals
- [ ] `evals/eval_runner.py`: runs all synthetic claims through the graph, checks:
      - routing correctness (does it land on the expected outcome)
      - payout math correctness
      - policy citation present when covered/excluded
      - image-narrative mismatch caught when it should be
- [ ] Aim for 20–30 cases minimum; report pass rate
- [ ] Fix failures by adjusting agent prompts/logic, not by hardcoding the expected answer

## Phase 9 — Wrap-up
- [ ] README.md: architecture diagram (can be ASCII or a simple image), how to run backend +
      frontend, how to run evals
- [ ] Write a short case study for your resume/portfolio: what the harness does, why the guardrails
      matter (rules 1–6 in CLAUDE.md), and your eval pass rate

## Notes for Claude Cowork
- Follow CLAUDE.md rules exactly — especially: no free-text handoffs between agents, mandatory
  audit logging, policy citations required, human-review is a hard gate not an LLM decision.
- Build and test one phase fully before starting the next.
- If context gets long, summarize progress into a short status note and continue rather than
  re-reading the whole history.
