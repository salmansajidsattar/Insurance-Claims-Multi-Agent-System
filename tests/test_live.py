"""Quick check with the REAL models. Skipped unless RUN_LIVE=1 (PowerShell: $env:RUN_LIVE="1")."""
import os

import pytest

from app.pipeline import process_claim
from app.schemas import Submission
from tests.fakes import SAMPLES, fakes_off

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE", "").strip() != "1", reason="set RUN_LIVE=1")


@pytest.mark.parametrize("claim_id", ["CLM-EVAL-001", "CLM-EVAL-010", "CLM-EVAL-017"])
def test_real_models(claim_id):
    fakes_off()
    s = process_claim(Submission(**SAMPLES[claim_id]["submission"]), claim_id)
    print(claim_id, s.decision.outcome, s.decision.reasons)
    assert not s.failures, [a.error for a in s.audit if a.error]
