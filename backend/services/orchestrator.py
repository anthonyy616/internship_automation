"""
Orchestrator service that coordinates all agents and manages the autonomous workflow.
"""

import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import uuid4

from backend.config import settings
from backend.models import SessionConfig, ScrapedJob, BotStatus
from backend.database import db
from backend.websocket_manager import ws_manager, Logger
from backend.services.job_scraper import JobScraperService
from backend.services.email_service import EmailService


class BotOrchestrator:
    """
    Main orchestrator that manages the entire autonomous workflow:
    1. Scrape jobs from multiple regions concurrently
    2. Validate and save jobs to database
    3. Apply to jobs (placeholder - requires per-site implementation)
    4. Send cold emails after applications
    """
    
    def __init__(self):
        self.status = BotStatus.IDLE
        self.session_id: Optional[str] = None
        self.session_config: Optional[SessionConfig] = None
        self.logger = Logger(ws_manager, db)
        self._stop_requested = False
        self._task: Optional[asyncio.Task] = None
        
        # Statistics
        self.jobs_found = 0
        self.applications_sent = 0
        self.emails_sent = 0
    
    @property
    def is_running(self) -> bool:
        return self.status == BotStatus.RUNNING
    
    async def start(self, config: SessionConfig) -> Dict[str, Any]:
        """Start the autonomous bot session."""
        if self.is_running:
            return {'success': False, 'error': 'Bot is already running'}
        
        self.session_config = config
        self.session_id = str(uuid4())
        self._stop_requested = False
        self.jobs_found = 0
        self.applications_sent = 0
        self.emails_sent = 0
        
        # Create session in database
        await db.create_session(
            regions=config.regions,
            config_snapshot=config.model_dump()
        )
        
        # Start the main task
        self._task = asyncio.create_task(self._run_autonomous_flow())
        
        await self.logger.info('SYSTEM', 'Bot session started', metadata={
            'session_id': self.session_id,
            'regions': config.regions
        })
        
        return {
            'success': True,
            'session_id': self.session_id,
            'message': f'Started bot for regions: {", ".join(config.regions)}'
        }
    
    async def stop(self, reason: str = 'User requested stop') -> Dict[str, Any]:
        """Stop the bot gracefully."""
        if not self.is_running:
            return {'success': False, 'error': 'Bot is not running'}
        
        self._stop_requested = True
        self.status = BotStatus.STOPPED
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        # End session in database
        if self.session_id:
            await db.end_session(self.session_id, 'stopped')
        
        await self.logger.warning('SYSTEM', f'Bot stopped: {reason}')
        await ws_manager.send_status_update('stopped', reason)
        
        return {
            'success': True,
            'message': 'Bot stopped',
            'stats': {
                'jobs_found': self.jobs_found,
                'applications_sent': self.applications_sent,
                'emails_sent': self.emails_sent
            }
        }
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current bot status."""
        return {
            'status': self.status.value,
            'session_id': self.session_id,
            'jobs_found': self.jobs_found,
            'applications_sent': self.applications_sent,
            'emails_sent': self.emails_sent,
            'is_running': self.is_running
        }
    
    async def _run_autonomous_flow(self):
        """Main autonomous workflow."""
        self.status = BotStatus.RUNNING
        await ws_manager.send_status_update('running', 'Starting autonomous job search...')
        
        try:
            config = self.session_config
            
            # Phase 1: Scrape jobs from all regions
            await self.logger.info('SYSTEM', 'Phase 1: Searching for jobs...')
            
            async def log_wrapper(level, action, message, region=None, metadata=None):
                await self.logger.log(level, action, message, region, metadata)
            
            scraper = JobScraperService(log_callback=log_wrapper)
            
            all_jobs: Dict[str, List[ScrapedJob]] = await scraper.scrape_all_regions(
                regions=config.regions,
                keywords=config.keywords
            )
            
            # Phase 2: Save jobs to database
            await self.logger.info('SYSTEM', 'Phase 2: Saving jobs to database...')
            
            saved_jobs = []
            for region, jobs in all_jobs.items():
                for job in jobs:
                    if self._stop_requested:
                        break
                    
                    # Check if job already exists
                    exists = await db.job_exists(job.url)
                    if exists:
                        continue
                    
                    # Save to database
                    job_data = {
                        'company': job.company,
                        'title': job.title,
                        'url': job.url,
                        'region': region,
                        'source': job.source,
                        'description': job.description or '',
                        'contact_email': job.contact_email,
                        'hiring_manager': job.hiring_manager,
                        'status': 'found'
                    }
                    
                    result = await db.create_job(job_data)
                    if result:
                        self.jobs_found += 1
                        saved_jobs.append({**job_data, 'id': result['id']})
                        
                        # Notify UI
                        await ws_manager.send_job_update(job_data, 'created')
            
            await self.logger.success('SYSTEM', f'Saved {self.jobs_found} new jobs to database')
            
            # Phase 3: Apply to jobs (simplified - marks as applied)
            await self.logger.info('SYSTEM', 'Phase 3: Processing applications...')
            
            # Note: Full form-filling automation would require per-site implementations
            # For now, we mark jobs as "applied" and proceed to email
            for job_data in saved_jobs[:config.max_applications]:
                if self._stop_requested:
                    break
                
                # Mark as applied (in real scenario, would fill forms)
                await db.update_job_status(job_data['id'], 'applied')
                self.applications_sent += 1
                
                await self.logger.success(
                    'APPLY',
                    f"Marked application: {job_data['title']} at {job_data['company']}",
                    job_data['region']
                )
                
                # Small delay
                await asyncio.sleep(1)
            
            # Phase 4: Send cold emails
            if not config.dry_run:
                await self.logger.info('SYSTEM', 'Phase 4: Sending cold emails...')
                
                email_service = EmailService(
                    smtp_user=settings.smtp_user,
                    smtp_password=settings.smtp_password,
                    smtp_server=settings.smtp_server,
                    smtp_port=settings.smtp_port,
                    log_callback=log_wrapper
                )
                
                for job_data in saved_jobs[:config.max_emails]:
                    if self._stop_requested:
                        break
                    
                    # Create ScrapedJob from saved data
                    job = ScrapedJob(
                        company=job_data['company'],
                        title=job_data['title'],
                        url=job_data['url'],
                        region=job_data['region'],
                        source=job_data['source'],
                        contact_email=job_data.get('contact_email'),
                        hiring_manager=job_data.get('hiring_manager')
                    )
                    
                    # Compose email
                    email_content = email_service.compose_email(
                        job=job,
                        candidate_name=config.user_name,
                        candidate_year=config.user_year,
                        candidate_major=config.user_major,
                        candidate_skills=config.user_skills,
                        university=config.user_university,
                        portfolio_url=config.portfolio_url,
                        contact_email=config.contact_email
                    )
                    
                    # Save email to database
                    email_record = await db.create_email({
                        'job_id': job_data['id'],
                        'recipient_email': email_content.recipient,
                        'subject': email_content.subject,
                        'body': email_content.body,
                        'status': 'pending'
                    })
                    
                    # Send email
                    result = await email_service.send_email(
                        recipient=email_content.recipient,
                        subject=email_content.subject,
                        body=email_content.body,
                        resume_path=settings.resume_path,
                        dry_run=config.dry_run
                    )
                    
                    # Update email status
                    if email_record:
                        status = 'sent' if result.get('success') else 'failed'
                        await db.update_email_status(
                            email_record['id'],
                            status,
                            result.get('error')
                        )
                    
                    if result.get('success'):
                        self.emails_sent += 1
                        await db.update_job_status(job_data['id'], 'emailed')
                    
                    # Delay between emails
                    await asyncio.sleep(5)
            
            # Session complete
            self.status = BotStatus.IDLE
            
            await self.logger.success('SYSTEM', 'Session completed successfully!', metadata={
                'jobs_found': self.jobs_found,
                'applications_sent': self.applications_sent,
                'emails_sent': self.emails_sent
            })
            
            # Update session stats
            if self.session_id:
                await db.update_session(self.session_id, {
                    'jobs_found': self.jobs_found,
                    'applications_sent': self.applications_sent,
                    'emails_sent': self.emails_sent
                })
                await db.end_session(self.session_id, 'completed')
            
            await ws_manager.send_status_update('idle', 'Session completed')
            await ws_manager.send_stats_update(await db.get_stats())
            
        except asyncio.CancelledError:
            self.status = BotStatus.STOPPED
            raise
        except Exception as e:
            self.status = BotStatus.ERROR
            await self.logger.error('SYSTEM', f'Bot error: {str(e)}')
            await ws_manager.send_status_update('error', str(e))
            
            if self.session_id:
                await db.end_session(self.session_id, 'failed')


# Global orchestrator instance
orchestrator = BotOrchestrator()
