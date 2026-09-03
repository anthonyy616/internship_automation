"""
Email composer — builds the follow-up cold email for an application.

Contact address priority:
    1. job.contact_email (explicit, from the source)
    2. domain guessing (careers@<domain>, jobs@<domain>, hr@<domain>) —
       only when config `email.allow_domain_guess` is true, and never for
       ATS hosts (greenhouse.io etc.) where the domain is not the employer's.

Body: LLM-personalized when a job description exists and an API key is
configured; otherwise a conservative template. The LLM self-check in
self_check.py validates the draft before anything is sent.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

ATS_HOSTS = {"greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com",
             "wd5.myworkdayjobs.com", "smartrecruiters.com", "myworkdaysite.com"}

TEMPLATE_BODY = """\
Hi {company} team,

I'm {name}, a {year} Computer Engineering student at {university} specializing in {skills}.

I'm very interested in the {role} role at {company} and believe my background in {skills} makes me a strong fit. You can see my work at {portfolio}.

I've attached my resume for your consideration. I would love the opportunity to discuss how I can contribute to your team.

Best regards,
{name}
"""


@dataclass
class EmailDraft:
    to_address: str
    subject: str
    body: str
    source: str = "template"   # 'template' | 'llm'


@dataclass
class SendResult:
    success: bool
    to_address: str = ""
    status: str = "sent"       # sent | blocked | failed
    reason: str = ""


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def guess_contact_emails(job) -> List[str]:
    """Best-effort contact addresses for a job."""
    emails: List[str] = []

    if getattr(job, "contact_email", None):
        emails.append(job.contact_email)

    url = getattr(job, "url", "") or ""
    host = (urlparse(url).hostname or "").lower().replace("www.", "")
    is_ats_host = any(host == a or host.endswith("." + a) for a in ATS_HOSTS)
    if host and not is_ats_host:
        for prefix in ("careers", "jobs", "hr", "info"):
            emails.append(f"{prefix}@{host}")

    return emails


class EmailComposer:
    """Build EmailDraft objects from an application + job."""

    def __init__(self, config_service=None, allow_domain_guess: Optional[bool] = None):
        from backend.services.config_service import ConfigService

        self.config_service = config_service or ConfigService()
        self._allow_domain_guess = allow_domain_guess

    async def _allow_guess(self) -> bool:
        if self._allow_domain_guess is not None:
            return self._allow_domain_guess
        cfg = await self.config_service.get_email_config()
        return bool(getattr(cfg, "allow_domain_guess", False))

    async def compose(self, application, job, profile) -> Optional[EmailDraft]:
        """Compose a draft, or None when no contact address can be found."""
        candidates = guess_contact_emails(job)
        if not candidates:
            return None

        if not await self._allow_guess():
            # Without guessing, only an explicit contact_email is usable
            explicit = getattr(job, "contact_email", None)
            if not explicit:
                return None
            candidates = [explicit]

        to_address = candidates[0]
        company = getattr(job, "company", "") or "the company"
        role = getattr(job, "title", "") or "the role"
        name = getattr(profile, "name", "") or "there"
        subject = f"Application: {role} at {company}"

        # LLM personalization (best-effort; falls back to the template)
        description = getattr(job, "description", "") or ""
        body = await self._llm_body(job, profile, role, company, description) \
            if description.strip() else self._template_body(profile, role, company)

        return EmailDraft(
            to_address=to_address,
            subject=subject,
            body=body,
            source="llm" if description.strip() else "template",
        )

    def _template_body(self, profile, role: str, company: str) -> str:
        from backend.config import settings

        skills = ", ".join(getattr(profile, "skills", [])[:3]) or "software engineering"
        year = "junior"
        if hasattr(profile, "university_year") and profile.university_year:
            year = profile.university_year
        return TEMPLATE_BODY.format(
            name=getattr(profile, "name", ""),
            year=year,
            university=getattr(profile, "university", ""),
            skills=skills,
            role=role,
            company=company,
            portfolio=getattr(profile, "portfolio_url", "") or "my portfolio",
        )

    async def _llm_body(self, job, profile, role: str, company: str, description: str) -> str:
        """LLM-personalized body with strict anti-hallucination constraints."""
        from backend.config import settings

        if not settings.openai_api_key:
            return self._template_body(profile, role, company)

        try:
            import asyncio
            from langchain_openai import ChatOpenAI
            from langchain_core.prompts import ChatPromptTemplate

            llm = ChatOpenAI(model=settings.openai_model, temperature=0.4,
                             api_key=settings.openai_api_key)
            prompt = ChatPromptTemplate.from_messages([
                ("system", (
                    "Write a short cold email (max 120 words) from {name} to {company} "
                    "applying for the {role} role. Only use facts from the candidate "
                    "profile below and the job description snippet. Never invent "
                    "experience, companies, or achievements. No placeholders, no "
                    "brackets. Use the candidate's real name in the signature."
                )),
                ("user", (
                    "Candidate profile: name={name}, university={university}, major={major}, "
                    "skills={skills}, portfolio={portfolio}\n\n"
                    "Job description snippet:\n{description}\n\n"
                    "Write the email body (no subject line)."
                )),
            ])
            chain = prompt | llm
            body = await asyncio.to_thread(
                chain.invoke,
                {
                    "name": getattr(profile, "name", ""),
                    "company": company,
                    "role": role,
                    "university": getattr(profile, "university", ""),
                    "major": getattr(profile, "major", ""),
                    "skills": ", ".join(getattr(profile, "skills", [])[:5]),
                    "portfolio": getattr(profile, "portfolio_url", ""),
                    "description": description[:1500],
                },
            )
            text = getattr(body, "content", str(body)).strip()
            if len(text) > 30:
                return text
        except Exception:
            pass
        return self._template_body(profile, role, company)