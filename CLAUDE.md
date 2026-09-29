# CLAUDE.md — Insurance Claims Multi-Agent System

## Project
AI-powered insurance claims pipeline. User uploads a vehicle damage photo + short incident
description (max 100 words). A 6-agent pipeline (incl. a policy RAG cross-check) decides **Auto-Approve / Human Review / Reject**
with a full audit trail. Built as a harness-engineering project — guardrails and verification
matter as much as the agents themselves.

## Stack
- Backend: **FastAPI** (Python 3.11+)
- Frontend: **Streamlit**
- Orchestration: **LangGraph** (StateGraph)
- Schema validation: **Pydantic v2**
- Vision: **local Qwen2.5-VL** (default `qwen2.5vl:7b` via Ollama, OpenAI-compatible API) for damage extraction; text agents use a local Qwen2.5 model on the same server
- Policy RAG: hybrid retrieval (BM25 + `nomic-embed-text` embeddings via Ollama, reciprocal rank fusion) over `data/policy_docs/*.md`
- Storage: SQLite (or flat JSON for v1) — claims, policies, repair estimates, audit logs
- Eval: pytest-based eval suite (routing correctness, payout math, policy correctness, image-claim alignment)

## Architecture rules (hard constraints — do not violate)
1. **No unstructured text between agents.** Every agent-to-agent handoff is a validated Pydantic
   model. Never pass a raw string as "context" to the next agent.
2. **Mandatory audit logging.** Every agent call logs: input, output, reasoning/confidence,
   timestamp, agent name. Stored keyed by `claim_id`. No agent runs without producing a log entry.
3. **Zero hallucinated policy rules.** The Policy Agent must cite the exact clause (from the
   retrieved policy JSON/doc) behind every coverage or exclusion decision. It must never invent a
   rule that isn't present in the source policy data. The PolicyRAG agent may only cite clauses it
   retrieved and must quote them word-for-word (checked in code). If the rules engine and the
   PolicyRAG agent disagree, the claim goes to `human_review`.
4. **Human-in-the-loop is a hard gate, not a suggestion.** Any claim with (a) risk_score above
   threshold, (b) image-narrative mismatch, or (c) payout above a configured limit MUST route to
   `human_review`. This is enforced by explicit conditional logic in the LangGraph router — never
   left to an LLM's judgment call.
5. **Deterministic routing.** The three outcomes (auto-approve / human-review / reject) are decided
   by explicit code in the router node, reading validated Pydantic fields — never by asking an LLM
   "what should happen next."
6. **Fail closed, not open.** If any agent's output fails Pydantic validation, retry once; if it
   fails again, route the claim to human review. Never let a malformed output pass through silently.

## Folder structure (keep it small — one simple file per job)
```
app/
  config.py     settings + risk weights
  schemas.py    all Pydantic models (ClaimState and every agent output)
  data.py       read data/ files, save/load claims (audit trail)
  llm.py        Ollama chat + embeddings (fakeable)
  rag.py        hybrid search over policy wording (BM25 + embeddings)
  agents.py     the 6 agents + run_agent() (retry, audit, fail closed)
  router.py     approve / review / reject
  pipeline.py   LangGraph wiring + process_claim()
  api.py        FastAPI
  ui.py         Streamlit
tests/  fakes.py test_pipeline.py test_api.py test_live.py eval.py
data/   policies.json policy_docs/*.md history.json estimates.json claims.json images/ audit/
docs/   pipeline.svg pipeline.png CASE_STUDY.md
```

## Agent contracts (all in app/schemas.py)
- **intake**: description → `Claim` (incident_type, zones, severity, third_party, circumstances)
- **damage**: photo + `Claim` → `Damage` (damage_type, severity, zones, confidence, matches_story, mismatch_reasons)
- **policy** (rules): `Claim` + package → `PolicyDecision` (covered, reasons, exclusions, limit, deductible, cited)
- **policy_rag**: `Claim` + retrieved wording → `RagDecision` (retrieved, covered, exclusions, cited, quotes, agrees)
- **risk**: `Claim` + `Damage` + history → `Risk` (score, flags)
- **payout**: `PolicyDecision` + estimate → `Payout` (estimate, eligible, deductible, net, capped)

## Coding conventions
- Python 3.11+, type hints everywhere, Pydantic v2 for all inter-agent data.
- One agent = one function `agent(state: ClaimState) -> ClaimState` in app/agents.py.
- Keep code simple: plain functions, no classes except Pydantic models.
- Agents never call each other directly — only through the graph.
- Every LLM call is wrapped with schema validation + one retry; on repeated failure, escalate to
  human review (rule 6 above).

## Testing
- Every new agent needs at least 3 eval cases before it's considered done: one auto-approve case,
  one human-review case, one reject case.
- Run `pytest` before treating any change as complete.

## What NOT to do
- Don't let any agent's final output be free text — always a validated Pydantic object.
- Don't skip audit logging to save time — it's required on every run, not just the final decision.
- Don't hardcode policy rules in code — always load from `policies.json` and cite the clause used.
