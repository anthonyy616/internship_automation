"""
Source Adapter Interface.

Every job source — API-based or scrape-based — implements this interface.
Adding/removing a source is one adapter file + a config toggle.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class JobListing:
    """Standardized job listing from any source."""
    source: str                           # 'remotive', 'arbeitnow', etc.
    external_id: Optional[str] = None     # source-specific dedup key
    title: str = ""
    company: str = ""
    url: str = ""
    description: str = ""
    region: str = ""                      # 'EU', 'UK', 'Nigeria', 'Turkiye'
    location: str = ""
    tags: List[str] = field(default_factory=list)
    contact_email: Optional[str] = None
    hiring_manager: Optional[str] = None


class SourceAdapter(ABC):
    """
    Abstract base class for all job source adapters.

    Subclass this and implement search() to add a new source.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this source (e.g., 'remotive')."""
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Either 'api' or 'scrape'."""
        ...

    @property
    def base_url(self) -> str:
        """Base URL of the source (for health tracking)."""
        return ""

    @abstractmethod
    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        """
        Search this source for jobs matching keywords in a region.

        Args:
            keywords: Search terms (e.g., ['Software Engineer Intern', 'ML Intern'])
            region: Target region (e.g., 'EU', 'UK', 'Nigeria', 'Turkiye')

        Returns:
            List of JobListing objects. Empty list if nothing found.
        """
        ...

    async def health_check(self) -> bool:
        """
        Optional: verify the source is reachable.
        Override for sources that need periodic health checks.
        """
        return True
