"""
ATS platform detector — identifies which applicant tracking system a job
URL belongs to, so the right Tier-1 adapter can be used.
"""

from typing import Optional


class ATSDetector:
    """Detect the ATS platform from a job URL or page host."""

    PATTERNS = {
        "greenhouse": ["boards.greenhouse.io", "greenhouse.io"],
        "lever": ["jobs.lever.co", "lever.co"],
        "workday": ["myworkdaysite.com", "myworkdayjobs.com", "wd5.myworkdayjobs.com", "workday.com"],
        "ashby": ["ashbyhq.com", "jobs.ashbyhq.com"],
        "smartrecruiters": ["smartrecruiters.com"],
    }

    @classmethod
    def detect(cls, url: str) -> Optional[str]:
        """Return the platform name for a URL, or None if unknown."""
        url_lower = url.lower()
        for platform, patterns in cls.PATTERNS.items():
            if any(p in url_lower for p in patterns):
                return platform
        return None


ats_detector = ATSDetector()