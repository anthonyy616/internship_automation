"""
Email service for sending personalized cold emails.
"""

import os
import smtplib
import asyncio
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from typing import Optional, Dict, Any, Callable
from pathlib import Path

from backend.models import EmailContent, ScrapedJob


class EmailService:
    """
    Handles cold email composition and sending.
    """
    
    # Email template with placeholders
    EMAIL_TEMPLATE = """Hi {hiring_contact},

I just submitted my application for the {position_title} position at {company_name} today ({current_date}).

I'm {candidate_name}, a {candidate_year} {candidate_major} major at {university}. {personal_hook}

My experience includes working with {top_skills}, and I'm particularly excited about the opportunity to contribute to {company_name}'s work.

I've attached my resume and you can also view my portfolio at: {portfolio_url}

Would love the opportunity to discuss how my background could support your team. Feel free to reach me at {contact_email}.

Best regards,
{candidate_name}
"""

    SUBJECT_TEMPLATE = "Application Follow-up: {position_title} | {candidate_name}"
    
    def __init__(
        self,
        smtp_user: str,
        smtp_password: str,
        smtp_server: str = 'smtp.gmail.com',
        smtp_port: int = 587,
        log_callback: Optional[Callable] = None
    ):
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.log = log_callback or (lambda *args, **kwargs: None)
    
    def _generate_personal_hook(self, job: ScrapedJob, skills: list) -> str:
        """Generate a personalized hook based on job and skills match."""
        hooks = [
            f"I've been following {job.company}'s growth and was excited to see this opening",
            f"Your work in the tech industry aligns perfectly with my passion for innovation",
            f"I'm drawn to {job.company}'s mission and believe I can contribute meaningfully",
            f"The {job.title} role caught my attention as it aligns with my career goals",
        ]
        
        # Simple rotation based on company name hash
        hook_index = hash(job.company) % len(hooks)
        return hooks[hook_index]
    
    def compose_email(
        self,
        job: ScrapedJob,
        candidate_name: str,
        candidate_year: str,
        candidate_major: str,
        candidate_skills: list,
        university: str,
        portfolio_url: str,
        contact_email: str
    ) -> EmailContent:
        """Compose a personalized cold email for a job."""
        
        # Determine hiring contact
        hiring_contact = job.hiring_manager if job.hiring_manager else f"{job.company} Hiring Team"
        
        # Get top 3 relevant skills
        top_skills = ', '.join(candidate_skills[:3])
        
        # Generate personal hook
        personal_hook = self._generate_personal_hook(job, candidate_skills)
        
        # Format current date
        current_date = datetime.now().strftime("%B %d, %Y")
        
        # Compose email body
        body = self.EMAIL_TEMPLATE.format(
            hiring_contact=hiring_contact,
            position_title=job.title,
            company_name=job.company,
            current_date=current_date,
            candidate_name=candidate_name,
            candidate_year=candidate_year,
            candidate_major=candidate_major,
            university=university,
            personal_hook=personal_hook,
            top_skills=top_skills,
            portfolio_url=portfolio_url,
            contact_email=contact_email
        )
        
        # Compose subject
        subject = self.SUBJECT_TEMPLATE.format(
            position_title=job.title,
            candidate_name=candidate_name
        )
        
        # Determine recipient email
        recipient = job.contact_email if job.contact_email else f"careers@{self._extract_domain(job.company)}"
        
        return EmailContent(
            subject=subject,
            body=body,
            recipient=recipient,
            job_id=''  # Will be set when saved to DB
        )
    
    def _extract_domain(self, company_name: str) -> str:
        """Extract a likely domain from company name."""
        # Simple heuristic: lowercase, remove spaces, add .com
        clean = company_name.lower().replace(' ', '').replace(',', '').replace('.', '')
        clean = ''.join(c for c in clean if c.isalnum())
        return f"{clean[:20]}.com"
    
    async def send_email(
        self,
        recipient: str,
        subject: str,
        body: str,
        resume_path: Optional[str] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Send an email via SMTP.
        
        Returns:
            Dict with 'success', 'message', and optionally 'error'
        """
        if dry_run:
            await self.log('INFO', 'EMAIL', f'[DRY RUN] Would send to: {recipient}')
            return {'success': True, 'message': 'Dry run - email not sent', 'dry_run': True}
        
        if not self.smtp_user or not self.smtp_password:
            return {'success': False, 'error': 'SMTP credentials not configured'}
        
        try:
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.smtp_user
            msg['To'] = recipient
            msg['Subject'] = subject
            
            # Attach body
            msg.attach(MIMEText(body, 'plain'))
            
            # Attach resume if provided
            if resume_path and Path(resume_path).exists():
                with open(resume_path, 'rb') as f:
                    attachment = MIMEApplication(f.read(), _subtype='pdf')
                    attachment.add_header(
                        'Content-Disposition',
                        'attachment',
                        filename='resume.pdf'
                    )
                    msg.attach(attachment)
            
            # Send email
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._send_smtp(msg)
            )
            
            await self.log('SUCCESS', 'EMAIL', f'Email sent to {recipient}')
            return {'success': True, 'message': f'Email sent to {recipient}'}
            
        except Exception as e:
            error_msg = str(e)
            await self.log('ERROR', 'EMAIL', f'Failed to send to {recipient}: {error_msg}')
            return {'success': False, 'error': error_msg}
    
    def _send_smtp(self, msg: MIMEMultipart):
        """Send email via SMTP (blocking operation)."""
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
        except smtplib.SMTPAuthenticationError as e:
            raise Exception(f"SMTP Auth Failed - Check your app password. Error: {str(e)}")
        except smtplib.SMTPException as e:
            raise Exception(f"SMTP Error: {str(e)}")
        except Exception as e:
            raise Exception(f"Connection error: {str(e)}")
    
    async def send_batch(
        self,
        emails: list,
        resume_path: Optional[str] = None,
        dry_run: bool = False,
        delay_between: float = 5.0
    ) -> Dict[str, Any]:
        """
        Send multiple emails with delays between them.
        
        Returns:
            Dict with 'total', 'sent', 'failed', 'results'
        """
        results = []
        sent = 0
        failed = 0
        
        for i, email in enumerate(emails):
            if i > 0:
                await asyncio.sleep(delay_between)
            
            result = await self.send_email(
                recipient=email.recipient,
                subject=email.subject,
                body=email.body,
                resume_path=resume_path,
                dry_run=dry_run
            )
            
            results.append({
                'recipient': email.recipient,
                **result
            })
            
            if result.get('success'):
                sent += 1
            else:
                failed += 1
        
        return {
            'total': len(emails),
            'sent': sent,
            'failed': failed,
            'results': results
        }
