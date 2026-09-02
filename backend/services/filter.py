"""
Job Filter Service.

Deterministic filtering (no LLM needed):
- Keyword eligibility matching
- URL and external_id dedup
- Blocklist checks
- Seniority false-positive detection
"""

import re
from typing import List, Optional, Set

from backend.services.sources.base import JobListing


# Keywords that indicate the role is relevant
POSITIVE_KEYWORDS = [
    "intern", "internship", "junior", "entry level", "entry-level",
    "graduate", "student", "summer", "trainee", "apprentice", "coop",
]

# Keywords that indicate the role is NOT relevant
NEGATIVE_KEYWORDS = [
    "senior engineer", "senior developer", "lead engineer", "lead developer",
    "manager", "director", "principal", "staff engineer", "staff developer",
    "5+ years", "7+ years", "10+ years", "15+ years",
    "vp of", "head of", "chief",
]

# False-positive seniority words that appear in company names or titles
# but don't actually mean "senior role"
SENIORITY_FALSE_POSITIVES = [
    "senior design",       # "Senior Design Project" — academic term
    "senior thesis",
    "senior student",      # "Senior student" = 4th year undergrad
    "senior year",
]


class JobFilter:
    """
    Filters job listings for eligibility, dedup, and blocklist.

    Usage:
        flt = JobFilter()
        filtered = await flt.filter_jobs(jobs, seen_urls, blocklist_companies)
    """

    def __init__(self):
        pass

    def is_eligible(self, job: JobListing) -> bool:
        """
        Check if a job is relevant for an intern/junior candidate.
        Returns True if the job passes the filter.
        """
        title_lower = job.title.lower()
        desc_lower = job.description.lower() if job.description else ""
        combined = f"{title_lower} {desc_lower}"

        # Check negative keywords first
        for neg in NEGATIVE_KEYWORDS:
            if neg in combined:
                # Check for false positives
                is_false_positive = any(fp in combined for fp in SENIORITY_FALSE_POSITIVES)
                if not is_false_positive:
                    return False

        # Check positive keywords
        for pos in POSITIVE_KEYWORDS:
            if pos in combined:
                return True

        # If no positive keywords found but no negatives either, include it
        # (some postings don't explicitly say "intern" but are relevant)
        return True

    def deduplicate(
        self,
        jobs: List[JobListing],
        seen_urls: Optional[Set[str]] = None,
    ) -> List[JobListing]:
        """
        Remove duplicate jobs by URL and company+title combination.
        Also filters out jobs whose URLs are already in seen_urls.
        """
        seen_urls = seen_urls or set()
        seen_keys: Set[str] = set()
        unique: List[JobListing] = []

        for job in jobs:
            # Skip if URL already seen
            if job.url in seen_urls:
                continue

            # Dedup by company+title
            dedup_key = f"{job.company.lower().strip()}|{job.title.lower().strip()}"
            if dedup_key in seen_keys:
                continue

            seen_keys.add(dedup_key)
            seen_urls.add(job.url)
            unique.append(job)

        return unique

    def check_blocklist(
        self,
        job: JobListing,
        blocked_companies: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> bool:
        """
        Check if a job is on the blocklist.
        Returns True if blocked (should be excluded).
        """
        blocked_companies = blocked_companies or []
        blocked_domains = blocked_domains or []

        # Check company name
        company_lower = job.company.lower()
        for blocked in blocked_companies:
            if blocked.lower() in company_lower or company_lower in blocked.lower():
                return True

        # Check email domain
        if job.contact_email and "@" in job.contact_email:
            domain = job.contact_email.split("@")[-1].lower()
            for blocked_domain in blocked_domains:
                if blocked_domain.lower() == domain:
                    return True

        return False

    async def filter_jobs(
        self,
        jobs: List[JobListing],
        seen_urls: Optional[Set[str]] = None,
        blocked_companies: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> List[JobListing]:
        """
        Full filter pipeline: eligibility → blocklist → dedup.

        Returns only jobs that pass all filters.
        """
        result = []

        for job in jobs:
            # 1. Eligibility check
            if not self.is_eligible(job):
                continue

            # 2. Blocklist check
            if self.check_blocklist(job, blocked_companies, blocked_domains):
                continue

            result.append(job)

        # 3. Dedup
        result = self.deduplicate(result, seen_urls)

        return result


# Global instance
job_filter = JobFilter()
