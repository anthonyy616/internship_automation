"""
Pydantic models for API requests and responses.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


# ==================== ENUMS ====================

class Region(str, Enum):
    EU = "EU"
    UK = "UK"
    NIGERIA = "Nigeria"
    TURKIYE = "Turkiye"


class JobStatus(str, Enum):
    FOUND = "found"
    APPLIED = "applied"
    EMAILED = "emailed"
    REJECTED = "rejected"
    INTERVIEW = "interview"
    OFFER = "offer"


class ApplicationMethod(str, Enum):
    FORM_FILL = "form_fill"
    QUICK_APPLY = "quick_apply"
    EMAIL_ONLY = "email_only"
    MANUAL = "manual"


class EmailStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    OPENED = "opened"
    REPLIED = "replied"


class LogLevel(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    DEBUG = "DEBUG"


class LogAction(str, Enum):
    SEARCH = "SEARCH"
    SCRAPE = "SCRAPE"
    VALIDATE = "VALIDATE"
    APPLY = "APPLY"
    EMAIL = "EMAIL"
    ERROR = "ERROR"
    SYSTEM = "SYSTEM"


class BotStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


# ==================== REQUEST MODELS ====================

class StartBotRequest(BaseModel):
    """Request to start the bot."""
    regions: List[Region] = Field(..., description="Regions to search in")
    contact_email: str = Field(..., description="Email for companies to contact you")
    portfolio_url: Optional[str] = Field(None, description="Portfolio website URL")
    keywords: Optional[List[str]] = Field(None, description="Override default search keywords")
    max_applications: Optional[int] = Field(50, description="Max applications per session")
    max_emails: Optional[int] = Field(50, description="Max emails per session")
    dry_run: bool = Field(False, description="If true, don't actually apply or send emails")


class StopBotRequest(BaseModel):
    """Request to stop the bot."""
    reason: Optional[str] = Field(None, description="Reason for stopping")


# ==================== RESPONSE MODELS ====================

class JobResponse(BaseModel):
    """Job data response."""
    id: str
    company: str
    title: str
    url: str
    region: Region
    source: Optional[str] = None
    contact_email: Optional[str] = None
    hiring_manager: Optional[str] = None
    description: Optional[str] = None
    eligibility_verified: bool = False
    status: JobStatus = JobStatus.FOUND
    created_at: datetime
    updated_at: datetime


class ApplicationResponse(BaseModel):
    """Application data response."""
    id: str
    job_id: str
    applied_at: datetime
    method: ApplicationMethod
    status: str
    notes: Optional[str] = None


class EmailResponse(BaseModel):
    """Email data response."""
    id: str
    job_id: str
    recipient_email: str
    subject: str
    body: str
    sent_at: Optional[datetime] = None
    status: EmailStatus


class LogEntry(BaseModel):
    """Activity log entry."""
    id: Optional[str] = None
    timestamp: datetime
    level: LogLevel
    action: LogAction
    message: str
    region: Optional[str] = None
    metadata: Dict[str, Any] = {}


class StatsResponse(BaseModel):
    """Statistics response."""
    total_jobs: int = 0
    total_applications: int = 0
    total_emails: int = 0
    jobs_by_region: Dict[str, int] = {}
    jobs_by_status: Dict[str, int] = {}
    session_active: bool = False
    current_status: BotStatus = BotStatus.IDLE


class BotStatusResponse(BaseModel):
    """Bot status response."""
    status: BotStatus
    session_id: Optional[str] = None
    started_at: Optional[datetime] = None
    regions: List[str] = []
    jobs_found: int = 0
    applications_sent: int = 0
    emails_sent: int = 0
    current_action: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    timestamp: datetime
    database_connected: bool
    version: str = "1.0.0"


# ==================== INTERNAL MODELS ====================

class ScrapedJob(BaseModel):
    """Job data scraped from a website."""
    company: str
    title: str
    url: str
    region: str
    source: str
    description: Optional[str] = None
    contact_email: Optional[str] = None
    hiring_manager: Optional[str] = None
    apply_url: Optional[str] = None
    requirements: Optional[List[str]] = None


class EmailContent(BaseModel):
    """Generated email content."""
    subject: str
    body: str
    recipient: str
    job_id: str


class SessionConfig(BaseModel):
    """Configuration for a bot session."""
    regions: List[str]
    contact_email: str
    portfolio_url: str
    keywords: List[str]
    max_applications: int
    max_emails: int
    dry_run: bool
    user_name: str
    user_major: str
    user_year: str
    user_skills: List[str]
    user_university: str
