"""
Pre-send self-check — LLM validation of every draft before it goes out.

Blocks obvious embarrassment: hallucinated claims, wrong company/role,
generic bot-sounding copy, or placeholders. On failure the send is
aborted and the draft is flagged for review instead.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import List


@dataclass
class SelfCheckResult:
    passed: bool
    issues: List[str] = field(default_factory=list)
    confidence: float = 1.0


class EmailSelfCheck:
    """Validates a draft against the job posting and candidate profile."""

    def __init__(self, llm=None):
        self.llm = llm  # injectable for tests; default built lazily

    async def validate(self, draft, job, profile) -> SelfCheckResult:
        from backend.config import settings

        if not settings.openai_api_key:
            # No LLM configured — conservative local checks only
            return self._local_checks(draft, job)

        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_core.output_parsers import JsonOutputParser

            llm = self.llm or ChatOpenAI(
                model=settings.openai_model, temperature=0.0,
                api_key=settings.openai_api_key,
            )

            prompt = ChatPromptTemplate.from_messages([
                ("system", (
                    "You validate cold emails before they are sent. Check:\n"
                    "1. Does the email reference a REAL detail from the job posting (not generic filler)?\n"
                    "2. Are there any hallucinated claims about the candidate (jobs, companies, achievements not listed in the profile)?\n"
                    "3. Is the company name and role title correct?\n"
                    "4. Does it sound natural, not bot-generated?\n"
                    "5. Are there placeholders, brackets, or lorem-ipsum text?\n"
                    "Return JSON: {\"passed\": bool, \"issues\": [str], \"confidence\": float}"
                )),
                ("user", (
                    "Candidate profile (only facts): name={name}, university={university}, "
                    "major={major}, skills={skills}, portfolio={portfolio}\n\n"
                    "Job posting: company={company}, role={role}\n"
                    "Job description snippet: {description}\n\n"
                    "Email draft:\nSubject: {subject}\nBody: {body}\n\n"
                    "Return the JSON verdict."
                )),
            ])

            chain = prompt | llm | JsonOutputParser()
            result = await asyncio.to_thread(
                chain.invoke,
                {
                    "name": getattr(profile, "name", ""),
                    "university": getattr(profile, "university", ""),
                    "major": getattr(profile, "major", ""),
                    "skills": ", ".join(getattr(profile, "skills", [])[:5]),
                    "portfolio": getattr(profile, "portfolio_url", ""),
                    "company": getattr(job, "company", ""),
                    "role": getattr(job, "title", ""),
                    "description": (getattr(job, "description", "") or "")[:1500],
                    "subject": draft.subject,
                    "body": draft.body,
                },
            )
            return SelfCheckResult(
                passed=bool(result.get("passed", False)),
                issues=[str(i) for i in result.get("issues", [])],
                confidence=float(result.get("confidence", 0.5)),
            )
        except Exception:
            # LLM unavailable — conservative local fallback
            return self._local_checks(draft, job)

    def _local_checks(self, draft, job) -> SelfCheckResult:
        """Offline sanity checks (used when no LLM is configured)."""
        issues: List[str] = []
        body_lower = draft.body.lower()

        for marker in ("{{", "}}", "[insert", "lorem", "placeholder", "xxx", "todo:"):
            if marker in body_lower:
                issues.append(f"placeholder-like text: '{marker}'")

        company = (getattr(job, "company", "") or "").lower()
        if company and company not in body_lower:
            issues.append("company name missing from body")

        return SelfCheckResult(passed=not issues, issues=issues)


email_self_check = EmailSelfCheck()