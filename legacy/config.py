"""
Configuration management for the Internship Automation Bot.
Loads settings from config.yaml and environment variables.
"""

import os
import yaml
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"


class UserProfile(BaseModel):
    """User profile configuration."""
    name: str
    email: str
    university_year: str
    major: str
    skills: List[str]
    university: str = "European University of Lefke"
    portfolio_url: str = "https://anthonyy616.vercel.app"
    contact_email: str = ""  # Set via UI at runtime


class SearchCriteria(BaseModel):
    """Search criteria configuration."""
    keywords: List[str]
    locations: List[str]


class SafetyConfig(BaseModel):
    """Safety limits configuration."""
    max_actions_per_day: int = 200
    min_delay_seconds: int = 5
    max_delay_seconds: int = 15


class RegionJobBoard(BaseModel):
    """Job board configuration for a region."""
    name: str
    url: str
    search_params: Optional[str] = None


class RegionConfig(BaseModel):
    """Region-specific configuration."""
    enabled: bool = False
    job_boards: List[RegionJobBoard] = []
    google_location_filter: str = ""


class Settings(BaseModel):
    """Main application settings."""
    # User Profile
    user_profile: UserProfile
    
    # Search
    search_criteria: SearchCriteria
    
    # Safety
    safety: SafetyConfig
    
    # Paths
    resume_path: str = "./data/resume.pdf"
    
    # Environment variables
    openai_api_key: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    supabase_url: str = ""
    supabase_key: str = ""
    
    # Region configurations (default job boards)
    regions: dict = {}


def load_config() -> Settings:
    """Load configuration from YAML and environment variables."""
    
    # Load YAML config
    config_data = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            config_data = yaml.safe_load(f) or {}
    
    # Extract user profile with defaults
    user_profile_data = config_data.get('user_profile', {})
    user_profile = UserProfile(
        name=user_profile_data.get('name', 'User'),
        email=user_profile_data.get('email', ''),
        university_year=user_profile_data.get('university_year', 'Junior'),
        major=user_profile_data.get('major', 'Computer Science'),
        skills=user_profile_data.get('skills', []),
        university=user_profile_data.get('university', 'European University of Lefke'),
        portfolio_url=user_profile_data.get('portfolio_url', 'https://anthonyy616.vercel.app'),
    )
    
    # Extract search criteria
    search_data = config_data.get('search_criteria', {})
    search_criteria = SearchCriteria(
        keywords=search_data.get('keywords', ['Software Engineer Intern', 'Computer Engineer Intern', 'AI Engineer', 'Backend Engineer', 'Data Engineer Intern', 'Computer Science Intern']),
        locations=search_data.get('locations', ['Remote'])
    )
    
    # Extract safety config
    safety_data = config_data.get('safety', {})
    safety = SafetyConfig(
        max_actions_per_day=safety_data.get('max_actions_per_day', 200),
        min_delay_seconds=safety_data.get('min_delay_seconds', 5),
        max_delay_seconds=safety_data.get('max_delay_seconds', 15)
    )
    
    # Define default region job boards
    default_regions = {
        'EU': RegionConfig(
            enabled=False,
            google_location_filter='Europe',
            job_boards=[
                RegionJobBoard(name='LinkedIn EU', url='https://www.linkedin.com/jobs/search/', search_params='keywords={query}&location=European%20Union'),
                RegionJobBoard(name='Indeed DE', url='https://de.indeed.com/jobs', search_params='q={query}&l=Germany'),
                RegionJobBoard(name='Glassdoor', url='https://www.glassdoor.com/Job/europe-jobs-SRCH_IL.0,6_IN5.htm', search_params='keyword={query}'),
            ]
        ),
        'UK': RegionConfig(
            enabled=False,
            google_location_filter='United Kingdom',
            job_boards=[
                RegionJobBoard(name='LinkedIn UK', url='https://www.linkedin.com/jobs/search/', search_params='keywords={query}&location=United%20Kingdom'),
                RegionJobBoard(name='Indeed UK', url='https://uk.indeed.com/jobs', search_params='q={query}'),
                RegionJobBoard(name='RateMyPlacement', url='https://www.ratemyplacement.co.uk/search', search_params='keywords={query}'),
                RegionJobBoard(name='Prospects', url='https://www.prospects.ac.uk/jobs-and-work-experience/job-sectors', search_params=''),
            ]
        ),
        'Nigeria': RegionConfig(
            enabled=False,
            google_location_filter='Nigeria',
            job_boards=[
                RegionJobBoard(name='Jobberman', url='https://www.jobberman.com/jobs', search_params='q={query}'),
                RegionJobBoard(name='MyJobMag', url='https://www.myjobmag.com/jobs', search_params='q={query}'),
                RegionJobBoard(name='HotNigerianJobs', url='https://www.hotnigerianjobs.com', search_params='s={query}'),
            ]
        ),
        'Turkiye': RegionConfig(
            enabled=False,
            google_location_filter='Turkey',
            job_boards=[
                RegionJobBoard(name='Kariyer.net', url='https://www.kariyer.net/is-ilanlari', search_params='arama={query}'),
                RegionJobBoard(name='LinkedIn TR', url='https://www.linkedin.com/jobs/search/', search_params='keywords={query}&location=Turkey'),
                RegionJobBoard(name='Indeed TR', url='https://tr.indeed.com/jobs', search_params='q={query}'),
            ]
        ),
    }
    
    # Build settings
    settings = Settings(
        user_profile=user_profile,
        search_criteria=search_criteria,
        safety=safety,
        resume_path=config_data.get('paths', {}).get('resume', './data/resume.pdf'),
        openai_api_key=os.getenv('OPENAI_API_KEY', ''),
        smtp_user=os.getenv('SMTP_USER', ''),
        smtp_password=os.getenv('SMTP_PASSWORD', ''),
        smtp_server=os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
        smtp_port=int(os.getenv('SMTP_PORT', '587')),
        supabase_url=os.getenv('SUPABASE_URL', ''),
        supabase_key=os.getenv('SUPABASE_KEY', ''),
        regions={k: v.model_dump() for k, v in default_regions.items()}
    )
    
    return settings


# Global settings instance
settings = load_config()
