"""
Pydantic models for API requests/responses and database rows.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


# =============================================================================
# ENUMS
# =============================================================================

class Region(str, Enum):
    EU = "EU"
    UK = "UK"
    NIGERIA = "Nigeria"
    TURKIYE = "Turkiye"


class JobStatus(str, Enum):
    DISCOVERED = "discovered"
    FILTERED = "filtered"
    QUEUED = "queued"
    APPLIED = "applied"
    EMAILED = "emailed"
    FAILED = "failed"
    FAILED_NEEDS_MANUAL = "failed_needs_manual"


class ApplicationStatus(str, Enum):
    QUEUED = "queued"
    FILLING = "filling"
    PAUSED_AWAITING_INPUT = "paused_awaiting_input"
    APPLIED = "applied"
    FAILED = "failed"


class EventStatus(str, Enum):
    STARTED = "started"
    SUCCESS = "success"
    FAILED = "failed"
    ESCALATED = "escalated"


class EventStage(str, Enum):
    SCRAPE = "scrape"
    FILTER = "filter"
    APPLY = "apply"
    EMAIL = "email"
    SYSTEM = "system"


class AnswerCategory(str, Enum):
    A = "A"  # Fact only user can supply (visa, salary, etc.)
    B = "B"  # Generatable from resume + posting


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    TIMED_OUT = "timed_out"


class BotStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class LogLevel(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    DEBUG = "DEBUG"


class LogAction(str, Enum):
    SEARCH = "SEARCH"
    SCRAPE = "SCRAPE"
    FILTER = "FILTER"
    APPLY = "APPLY"
    EMAIL = "EMAIL"
    SYSTEM = "SYSTEM"
    ERROR = "ERROR"


# =============================================================================
# DATABASE ROW MODELS
# =============================================================================

class Job(BaseModel):
    id: str
    source: str
    external_id: Optional[str] = None
    title: str
    company: str
    region: str
    url: str
    description: Optional[str] = None
    status: str = "discovered"
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Application(BaseModel):
    id: str
    job_id: str
    status: str = "queued"
    applied_via: Optional[str] = None
    ats_platform: Optional[str] = None
    resume_version: Optional[str] = None
    filled_fields: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentEvent(BaseModel):
    id: str
    application_id: Optional[str] = None
    stage: str
    action: str
    target_url: Optional[str] = None
    status: str
    screenshot_url: Optional[str] = None
    duration_ms: Optional[int] = None
    error_text: Optional[str] = None
    metadata: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProfileAnswer(BaseModel):
    id: str
    question_text: str
    answer_text: str
    category: str = "B"
    times_used: int = 0
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PendingConfirmation(BaseModel):
    id: str
    application_id: str
    question_text: str
    field_type: Optional[str] = None
    options: Optional[List[str]] = None
    status: str = "pending"
    telegram_message_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    answered_at: Optional[datetime] = None


class EmailRecord(BaseModel):
    id: str
    application_id: Optional[str] = None
    to_address: str
    subject: str
    body: str
    self_check_status: Optional[str] = None
    self_check_notes: Optional[str] = None
    sent_at: Optional[datetime] = None
    bounced_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Source(BaseModel):
    id: str
    name: str
    type: str  # 'api' or 'scrape'
    enabled: bool = True
    base_url: Optional[str] = None
    last_success_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    error_count: int = 0
    consecutive_failures: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# API REQUEST MODELS
# =============================================================================

class StartBotRequest(BaseModel):
    regions: List[Region] = Field(..., description="Regions to search in")
    contact_email: str = Field(..., description="Email for companies to contact you")
    portfolio_url: Optional[str] = Field(None, description="Portfolio website URL")
    keywords: Optional[List[str]] = Field(None, description="Override default search keywords")
    max_applications: Optional[int] = Field(50, description="Max applications per session")
    max_emails: Optional[int] = Field(50, description="Max emails per session")
    dry_run: bool = Field(False, description="If true, don't actually apply or send emails")


class StopBotRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Reason for stopping")


class ConfigUpdateRequest(BaseModel):
    key: str
    value: Dict[str, Any]


class AnswerQuestionRequest(BaseModel):
    confirmation_id: str
    answer: str


# =============================================================================
# API RESPONSE MODELS
# =============================================================================

class JobResponse(BaseModel):
    id: str
    company: str
    title: str
    url: str
    region: str
    source: Optional[str] = None
    description: Optional[str] = None
    status: str = "discovered"
    created_at: datetime


class ApplicationResponse(BaseModel):
    id: str
    job_id: str
    status: str
    applied_via: Optional[str] = None
    ats_platform: Optional[str] = None
    created_at: datetime


class StatsResponse(BaseModel):
    total_jobs: int = 0
    total_applications: int = 0
    total_emails: int = 0
    jobs_by_region: Dict[str, int] = {}
    jobs_by_status: Dict[str, int] = {}
    session_active: bool = False
    current_status: str = "idle"


class BotStatusResponse(BaseModel):
    status: str
    session_id: Optional[str] = None
    jobs_found: int = 0
    applications_sent: int = 0
    emails_sent: int = 0


class HealthResponse(BaseModel):
    status: str = "healthy"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    database_connected: bool
    version: str = "2.0.0"


# =============================================================================
# INTERNAL MODELS (used by services, not stored directly)
# =============================================================================

class ScrapedJob(BaseModel):
    """Job data from a source adapter."""
    source: str
    external_id: Optional[str] = None
    title: str
    company: str
    url: str
    region: str = ""
    description: str = ""
    location: str = ""
    contact_email: Optional[str] = None
    hiring_manager: Optional[str] = None
    tags: List[str] = []


class ApplyResult(BaseModel):
    """Result of an application attempt."""
    success: bool
    screenshot_path: Optional[str] = None
    error: Optional[str] = None
    filled_fields: Dict[str, Any] = {}
    needs_input: bool = False
    input_field: Optional[str] = None


class EmailContent(BaseModel):
    """Generated email content."""
    subject: str
    body: str
    recipient: str
    job_id: str = ""


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


class SelfCheckResult(BaseModel):
    """Result of email self-check."""
    passed: bool
    issues: List[str] = []
    confidence: float = 0.0
