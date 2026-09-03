"""
Question classifier — decides whether a form question can be auto-answered
from the profile (Category B) or must be escalated to the user (Category A).

Category A: facts only the user can supply (visa, salary, dates, personal
            demographics) — these go to the pending_confirmations queue.
Category B: generatable from the profile (university, major, skills, ...).
"""

from typing import List

# Category A keywords — escalate when matched
CATEGORY_A_KEYWORDS: List[str] = [
    "visa", "work authorization", "work authorisation", "sponsorship", "sponsor",
    "salary", "compensation", "pay expectation", "rate", "notice period",
    "start date", "availability date", "available to start", "when can you start",
    "disability", "gender", "pronouns", "ethnicity", "race", "veteran", "citizen",
    "citizenship", "nationality", "legally authorized", "legally authorised",
    "work permit", "right to work", "referral", "how did you hear",
]


class QuestionClassifier:
    """Classify a question as Category A (escalate) or B (auto-answer)."""

    def classify(self, question: str, answer_available: bool = False) -> str:
        """Return 'A' (escalate) or 'B' (auto-answer)."""
        q = question.lower().strip()
        if not q:
            return "B"

        if any(kw in q for kw in CATEGORY_A_KEYWORDS):
            return "A"

        # A question we already have an answer for is always safe to fill
        if answer_available:
            return "B"

        # Unknown questions with no stored answer: escalate rather than guess
        return "A"


question_classifier = QuestionClassifier()