"""Settings. Values come from the .env file (see .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()

# Local models (Ollama)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
TEXT_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b")
VISION_MODEL = os.getenv("VLM_MODEL", "qwen2.5vl:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "120"))

# Router limits
RISK_LIMIT = float(os.getenv("RISK_THRESHOLD", "0.6"))           # risk >= this -> review
PAYOUT_LIMIT = float(os.getenv("AUTO_APPROVE_PAYOUT_LIMIT", "5000"))  # net > this -> review
MIN_CONFIDENCE = float(os.getenv("MIN_DAMAGE_CONFIDENCE", "0.7"))  # photo confidence below -> review

# RAG
TOP_K = int(os.getenv("RAG_TOP_K", "6"))

# Risk rules: flag -> weight. Score = sum of weights (max 1.0)
RISK_WEIGHTS = {
    "frequent_claims": 0.35,       # 3+ claims in the last 365 days
    "fraud_flag": 0.40,            # a past claim was flagged for fraud
    "photo_mismatch": 0.40,        # photo doesn't match the story
    "low_confidence": 0.20,        # vision model not sure
    "late_report": 0.15,           # reported after 14 days
    "new_policy": 0.20,            # incident within 30 days of policy start
    "no_police_report": 0.10,      # other party involved but no police report
}

# Folders
AUDIT_DIR = os.getenv("AUDIT_DIR", "data/audit")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "data/uploads")
