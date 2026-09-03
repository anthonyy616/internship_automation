"""
Job source adapters.

Every job source implements SourceAdapter. See base.py for the interface.
"""

from backend.services.sources.base import SourceAdapter, JobListing
from backend.services.sources.remotive import RemotiveAdapter
from backend.services.sources.arbeitnow import ArbeitnowAdapter
from backend.services.sources.hackernews import HackerNewsAdapter
from backend.services.sources.jobicy import JobicyAdapter
from backend.services.sources.jobberman import JobbermanAdapter
from backend.services.sources.myjobmag import MyJobMagAdapter
from backend.services.sources.eleman import ElemanAdapter
from backend.services.sources.prospects import ProspectsAdapter
from backend.services.sources.milkround import MilkroundAdapter

__all__ = [
    "SourceAdapter",
    "JobListing",
    "RemotiveAdapter",
    "ArbeitnowAdapter",
    "HackerNewsAdapter",
    "JobicyAdapter",
    "JobbermanAdapter",
    "MyJobMagAdapter",
    "ElemanAdapter",
    "ProspectsAdapter",
    "MilkroundAdapter",
]