"""
arq task queue workers.

Job lifecycle state machine (enforced by the workers):

    discovered -> filtered -> queued -> applying -> applied | failed
        -> emailed

Workers:
    scrape_source  — poll one source adapter, filter, persist jobs
    apply_to_job   — create an application, run the tiered applier
    send_email     — compose + send the follow-up cold email
"""

from backend.workers.scrape_worker import scrape_source
from backend.workers.apply_worker import apply_to_job
from backend.workers.email_worker import send_email

__all__ = ["scrape_source", "apply_to_job", "send_email"]