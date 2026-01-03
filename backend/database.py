"""
Supabase database client for the Internship Automation Bot.
Handles all database operations for jobs, applications, emails, and logs.
"""

import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Initialize Supabase client
SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', '')

_supabase_client: Optional[Client] = None


def get_supabase() -> Optional[Client]:
    """Get or create Supabase client instance."""
    global _supabase_client
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Warning: Supabase credentials not configured. Using local-only mode.")
        return None
    
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    return _supabase_client


class DatabaseService:
    """Database service for all CRUD operations."""
    
    def __init__(self):
        self.client = get_supabase()
    
    @property
    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self.client is not None
    
    # ==================== JOBS ====================
    
    async def create_job(self, job_data: Dict[str, Any]) -> Optional[Dict]:
        """Create a new job entry."""
        if not self.client:
            return None
        
        try:
            result = self.client.table('jobs').insert(job_data).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error creating job: {e}")
            return None
    
    async def get_jobs(self, region: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get jobs with optional filters."""
        if not self.client:
            return []
        
        try:
            query = self.client.table('jobs').select('*')
            
            if region:
                query = query.eq('region', region)
            if status:
                query = query.eq('status', status)
            
            result = query.order('created_at', desc=True).limit(limit).execute()
            return result.data or []
        except Exception as e:
            print(f"Error fetching jobs: {e}")
            return []
    
    async def update_job_status(self, job_id: str, status: str) -> bool:
        """Update job status."""
        if not self.client:
            return False
        
        try:
            self.client.table('jobs').update({
                'status': status,
                'updated_at': datetime.utcnow().isoformat()
            }).eq('id', job_id).execute()
            return True
        except Exception as e:
            print(f"Error updating job: {e}")
            return False
    
    async def job_exists(self, url: str) -> bool:
        """Check if a job with the given URL already exists."""
        if not self.client:
            return False
        
        try:
            result = self.client.table('jobs').select('id').eq('url', url).execute()
            return len(result.data) > 0 if result.data else False
        except Exception as e:
            print(f"Error checking job exists: {e}")
            return False
    
    # ==================== APPLICATIONS ====================
    
    async def create_application(self, app_data: Dict[str, Any]) -> Optional[Dict]:
        """Create a new application entry."""
        if not self.client:
            return None
        
        try:
            result = self.client.table('applications').insert(app_data).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error creating application: {e}")
            return None
    
    async def get_applications(self, job_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get applications with optional job_id filter."""
        if not self.client:
            return []
        
        try:
            query = self.client.table('applications').select('*')
            
            if job_id:
                query = query.eq('job_id', job_id)
            
            result = query.order('applied_at', desc=True).limit(limit).execute()
            return result.data or []
        except Exception as e:
            print(f"Error fetching applications: {e}")
            return []
    
    # ==================== EMAILS ====================
    
    async def create_email(self, email_data: Dict[str, Any]) -> Optional[Dict]:
        """Create a new email entry."""
        if not self.client:
            return None
        
        try:
            result = self.client.table('emails').insert(email_data).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error creating email: {e}")
            return None
    
    async def update_email_status(self, email_id: str, status: str, error_message: Optional[str] = None) -> bool:
        """Update email status after sending attempt."""
        if not self.client:
            return False
        
        try:
            update_data = {
                'status': status,
                'sent_at': datetime.utcnow().isoformat() if status == 'sent' else None
            }
            if error_message:
                update_data['error_message'] = error_message
            
            self.client.table('emails').update(update_data).eq('id', email_id).execute()
            return True
        except Exception as e:
            print(f"Error updating email: {e}")
            return False
    
    async def get_emails(self, job_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get emails with optional filters."""
        if not self.client:
            return []
        
        try:
            query = self.client.table('emails').select('*')
            
            if job_id:
                query = query.eq('job_id', job_id)
            if status:
                query = query.eq('status', status)
            
            result = query.order('sent_at', desc=True).limit(limit).execute()
            return result.data or []
        except Exception as e:
            print(f"Error fetching emails: {e}")
            return []
    
    # ==================== ACTIVITY LOGS ====================
    
    async def log_activity(
        self,
        level: str,
        action: str,
        message: str,
        region: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[Dict]:
        """Log an activity to the database."""
        if not self.client:
            return None
        
        try:
            log_data = {
                'level': level,
                'action': action,
                'message': message,
                'region': region,
                'metadata': metadata or {},
                'timestamp': datetime.utcnow().isoformat()
            }
            result = self.client.table('activity_logs').insert(log_data).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error logging activity: {e}")
            return None
    
    async def get_recent_logs(self, limit: int = 50, region: Optional[str] = None) -> List[Dict]:
        """Get recent activity logs."""
        if not self.client:
            return []
        
        try:
            query = self.client.table('activity_logs').select('*')
            
            if region:
                query = query.eq('region', region)
            
            result = query.order('timestamp', desc=True).limit(limit).execute()
            return result.data or []
        except Exception as e:
            print(f"Error fetching logs: {e}")
            return []
    
    # ==================== SESSIONS ====================
    
    async def create_session(self, regions: List[str], config_snapshot: Dict) -> Optional[Dict]:
        """Create a new bot session."""
        if not self.client:
            return None
        
        try:
            session_data = {
                'regions_targeted': regions,
                'config_snapshot': config_snapshot,
                'status': 'running',
                'jobs_found': 0,
                'applications_sent': 0,
                'emails_sent': 0
            }
            result = self.client.table('sessions').insert(session_data).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error creating session: {e}")
            return None
    
    async def update_session(self, session_id: str, updates: Dict[str, Any]) -> bool:
        """Update session statistics."""
        if not self.client:
            return False
        
        try:
            self.client.table('sessions').update(updates).eq('id', session_id).execute()
            return True
        except Exception as e:
            print(f"Error updating session: {e}")
            return False
    
    async def end_session(self, session_id: str, status: str = 'completed') -> bool:
        """End a session."""
        if not self.client:
            return False
        
        try:
            self.client.table('sessions').update({
                'status': status,
                'ended_at': datetime.utcnow().isoformat()
            }).eq('id', session_id).execute()
            return True
        except Exception as e:
            print(f"Error ending session: {e}")
            return False
    
    # ==================== STATISTICS ====================
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get overall statistics."""
        if not self.client:
            return {
                'total_jobs': 0,
                'total_applications': 0,
                'total_emails': 0,
                'jobs_by_region': {},
                'jobs_by_status': {}
            }
        
        try:
            # Get counts
            jobs = self.client.table('jobs').select('id, region, status').execute()
            applications = self.client.table('applications').select('id').execute()
            emails = self.client.table('emails').select('id, status').execute()
            
            jobs_data = jobs.data or []
            
            # Calculate stats
            jobs_by_region = {}
            jobs_by_status = {}
            for job in jobs_data:
                region = job.get('region', 'Unknown')
                status = job.get('status', 'unknown')
                jobs_by_region[region] = jobs_by_region.get(region, 0) + 1
                jobs_by_status[status] = jobs_by_status.get(status, 0) + 1
            
            return {
                'total_jobs': len(jobs_data),
                'total_applications': len(applications.data or []),
                'total_emails': len(emails.data or []),
                'jobs_by_region': jobs_by_region,
                'jobs_by_status': jobs_by_status
            }
        except Exception as e:
            print(f"Error fetching stats: {e}")
            return {
                'total_jobs': 0,
                'total_applications': 0,
                'total_emails': 0,
                'jobs_by_region': {},
                'jobs_by_status': {}
            }


# Global database service instance
db = DatabaseService()
