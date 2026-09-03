"""
Tiered auto-apply pipeline.

Tier 1: deterministic ATS adapters (greenhouse, lever, ashby, workday)
Tier 2: generic LLM-assisted form filling
Tier 3: honest failure -> failed_needs_manual + email-only follow-up
"""

from backend.services.applier.tiered import TieredApplier
from backend.services.applier.base import ApplyResult, ApplyContext
from backend.services.applier.detector import ATSDetector

__all__ = ["TieredApplier", "ApplyResult", "ApplyContext", "ATSDetector"]