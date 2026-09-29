# Case Study — Insurance Claims Multi-Agent Harness

**Role:** sole developer · **Stack:** Python, LangGraph, Pydantic v2, FastAPI, Streamlit,
local Qwen2.5-VL / Qwen2.5 / nomic-embed-text via Ollama, hybrid RAG (BM25 + embeddings) ·
**Code:** ~1,250 lines in 10 files + 123 tests

## The problem

Car insurance claims need a fast yes/no, but a wrong "yes" costs money and a wrong "no" hurts a
real customer. An LLM that reads a photo and a story and simply *decides* is fast, but you can't
audit it, it can invent policy rules, and it fails silently. The goal was a system that is fast
for clear cases and **safe by construction** for everything else.

## What I built

A 6-agent LangGraph pipeline — Intake → Damage Evidence → Policy (rules) → Policy RAG → Risk →
Payout → Router — that takes a damage photo and a ≤100-word description, checks it against the
customer's package (which perils are covered, the limit and deductible for each) and the
company's policy wording, and returns **auto-approve, human review or reject**, with a full
audit trail. A FastAPI backend runs claims in the background; a Streamlit
UI shows the verdict and a step-by-step agent timeline.

The key design choice: **models describe, code decides.**
- The LLM only extracts 5 structured facts from the text.
- The VLM only describes the photo (damage type, severity, zones).
- A second LLM reads the policy wording retrieved by hybrid search (BM25 + embeddings) and gives
  an independent coverage decision, quoting the clauses it used.
- Whether the photo matches the story, the rules-based coverage decision, the risk score, the
  payout, and the final route are all plain Python — repeatable and unit-tested. If the rules
  engine and the RAG reading disagree, a person decides.

## Why the guardrails matter

| Guardrail | What it prevents | Example from the eval set |
|---|---|---|
| Typed handoffs (Pydantic, `extra="forbid"`) | Free text leaking between agents; invented fields | Junk LLM output fails validation instead of reaching the Policy agent |
| Audit every attempt, saved after every step | "Why was this decided?" having no answer | Every claim has 6 entries; retries and failures are kept |
| Policy agent cites real clauses only | Hallucinated coverage rules | DUI claim cites `PREMIUM-EXC-DUI`; a citation not in the policy fails the step |
| RAG must quote word-for-word and agree with rules | A fluent but wrong reading of the policy | An invented quote or un-retrieved clause fails validation; a flipped RAG verdict sends the claim to review |
| Per-peril package limits | Paying the wrong amount | Vandalism on POL-004 uses its own $500 deductible, not the $750 collision one |
| Human review as a hard gate | Auto-approving suspicious or large claims | "Rear-ended" story + photo of a crushed front → review, not approve |
| Gates beat reject | Auto-rejecting a customer on a possibly wrong photo read | Flood claim on a no-flood policy, but the photo shows a crash → review |
| Deterministic router | An LLM "deciding what's next" | Router is ~30 lines of `if` statements on validated fields |
| Fail closed | Broken output passing silently | Model server down → claim still lands in human review with a full trail |

## Evaluation

- 30 synthetic claims (10 approve / 11 review / 9 reject) over 10 policies with real exclusions,
  claim histories with red flags, and itemised repair estimates. Every expected label is checked
  against the source data by a test, so the ground truth can't drift.
- Eval runner checks routing, reasons, payout math, citations, rules/RAG agreement, photo/story
  mismatch and agent failures, and reports a pass rate. A retrieval test checks the deciding
  clauses are always in the top 6.

| Mode | Result |
|---|---|
| Harness logic (ground-truth model answers) | **100%** (30/30) |
| End-to-end with local Qwen2.5-VL 7B + Qwen2.5 7B + nomic-embed-text | _fill in after `python -m tests.eval`_ |

The two numbers separate *harness correctness* from *model quality*: any live failure points at a
specific agent and check, and is fixed by changing prompts or matching rules — never by
hard-coding answers.

## What I'd do next

- Tune VLM prompts on the live eval failures; add more mismatch and pre-existing-damage cases.
- Reviewer actions in the UI (approve/override with a reason, logged to the audit trail).
- Swap JSON files for SQLite/Postgres; add a queue for background jobs.

## Resume bullets

- Built a 6-agent LangGraph claims pipeline (FastAPI + Streamlit, local Qwen2.5-VL) that routes
  claims to auto-approve / human review / reject with a per-step audit trail.
- Added a hybrid-RAG policy agent (BM25 + embeddings, rank fusion) whose verbatim-quoted coverage
  decision is cross-checked against a rules engine; disagreements go to human review.
- Designed "models describe, code decides" guardrails: typed Pydantic handoffs, clause-cited
  policy decisions with zero hallucinated rules, deterministic routing and fail-closed retries.
- Wrote 123 tests and a 30-case eval runner that separates harness correctness (100%) from
  model accuracy on live local models.
