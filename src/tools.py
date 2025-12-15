import time
import random
import urllib.parse
import sqlite3
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright
from langchain.tools import tool
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# You must install these: pip install playwright-stealth
from playwright_stealth import stealth_sync

class EmailTools:
    @tool("Send Email")
    def send_email(recipient: str, subject: str, body: str):
        """Sends an email using the SMTP configuration in .env. Returns success status."""
        sender = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASSWORD")
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        
        if not sender or not password:
            return "Error: SMTP credentials not set in .env"

        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)
            server.quit()
            return f"Email sent successfully to {recipient}"
        except Exception as e:
            return f"Failed to send email: {str(e)}"

class JobParser:
    @staticmethod
    def check_eligibility(text: str) -> dict:
        """
        Analyzes job description text for eligibility keywords.
        Returns a dict with 'eligible': bool, 'score': int, 'matches': list.
        """
        text_lower = text.lower()
        
        # Positive signals
        # "rising senior", "junior", "3rd year", "4th year", "2025", "undergraduate"
        positive_patterns = [
            r"junior", r"senior", r"3rd year", r"4th year", r"rising senior", 
            r"class of 2025", r"class of 2026", r"undergrad", r"bachelor"
        ]
        
        # Negative signals (optional, sometimes 'freshman' is listed as 'not for freshman')
        # We'll just look for existence of positives for now.
        
        matches = []
        for pattern in positive_patterns:
            if re.search(pattern, text_lower):
                matches.append(pattern)
        
        # Simple heuristic: Must have at least one year/status indicator AND "intern"
        is_intern = "intern" in text_lower
        has_status = len(matches) > 0
        
        eligible = is_intern and has_status
        
        return {
            "eligible": eligible,
            "is_intern": is_intern,
            "status_matches": matches,
            "reason": "Matches found: " + ", ".join(matches) if eligible else "Missing keywords."
        }

# You must install these: pip install playwright-stealth
from playwright_stealth import stealth_sync

# Database Helper (Inline to avoid complex relative imports if running as script, 
# or we could fix pythonpath. For now, we will connect directly.)
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'jobs.db')

class JobTools:
    @tool("Save Job")
    def save_job(company: str, title: str, url: str, email: str = None, summary: str = None):
        """Saves a found job opportunity to the database. Returns True if saved, False if duplicate."""
        try:
            conn = sqlite3.connect(DB_PATH)
            # Create table if not exists (redundant safety)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT,
                    title TEXT,
                    url TEXT UNIQUE,
                    status TEXT DEFAULT 'found',
                    contact_email TEXT,
                    notes TEXT
                )
            ''')
            
            conn.execute('''
                INSERT INTO jobs (company, title, url, contact_email, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (company, title, url, email, summary))
            conn.commit()
            conn.close()
            return "Job saved successfully."
        except sqlite3.IntegrityError:
            conn.close()
            return "Job already exists in database."
        except Exception as e:
            if 'conn' in locals(): conn.close()
            return f"Error saving job: {str(e)}"

    @tool("Validate Job Eligibility")
    def validate_job_text(text: str):
        """
        Checks if a job description text is suitable for a Junior/Senior undergraduate intern.
        Returns a detailed validation result.
        """
        return JobParser.check_eligibility(text)

    @tool("Read Resume")
    def read_resume():
        """Reads the user's resume summary or content. Assumes text file for simplicity or parses PDF."""
        # For this prototype, we'll try to read a text summary if it exists, or just return a placeholder
        # The user has data/resume.pdf. We might need pypdf to read it.
        # Let's just return the User Profile from config if we can't read the file easily here without config access.
        # THIS TOOL might be better placed in the agent definition using the config variable directly.
        pass

class BrowserTools:
    @tool("Scrape Website")
    def scrape_website(url: str):
        """Scrapes text content from a website using Playwright in stealth mode to avoid detection."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            # Apply stealth settings
            stealth_sync(context)
            
            page = context.new_page()
            
            # Random jitter to mimic human behavior
            time.sleep(random.uniform(1, 4))
            
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                
                # Scroll a bit to trigger lazy loading
                page.evaluate("window.scrollTo(0, 500)")
                time.sleep(random.uniform(1, 2))
                
                content = page.content()
                browser.close()
                
                # Use BeautifulSoup to clean the text
                soup = BeautifulSoup(content, 'html.parser')
                
                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.extract()
                    
                text = soup.get_text(separator='\n')
                
                # Break into lines and remove leading/trailing space on each
                lines = (line.strip() for line in text.splitlines())
                # Break multi-headlines into a line each
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                # Drop blank lines
                text = '\n'.join(chunk for chunk in chunks if chunk)
                
                return text[:10000] # Return safe limit
                
            except Exception as e:
                browser.close()
                return f"Error scraping {url}: {str(e)}"

    @tool("Search Jobs")
    def search_jobs(query: str):
        """Searches for jobs on Google using specific operators. Returns a list of URLs."""
        # This is a basic implementation. Ideally, use a SERP API like Serper or specialized scraping for better results.
        # But per requirements, we will try to scrape Google Search results using Playwright.
        
        # Prepare URL
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.google.com/search?q={encoded_query}&ibp=htl;jobs"
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            stealth_sync(context)
            page = context.new_page()
            
            time.sleep(random.uniform(2, 5))
            
            try:
                page.goto(search_url, timeout=60000)
                time.sleep(random.uniform(2, 4)) # Wait for results
                
                # Extract links (This selector logic is fragile and depends on Google's structure)
                # We will just look for standard search result links for now as a fallback
                # A robust solution would use a dedicated API.
                
                links = []
                # Try standard organic results
                results = page.query_selector_all('div.g a')
                for res in results:
                    href = res.get_attribute('href')
                    if href and 'http' in href and not 'google' in href:
                        links.append(href)
                        
                browser.close()
                return links[:5] # Limit to 5 for now
            except Exception as e:
                browser.close()
                return f"Error searching: {str(e)}"


    @tool("Apply Form Filler")
    def apply_to_job(url: str, form_data: dict):
        """Attempts to fill a standard application form."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False) # Headless=False to see what's happening
            context = browser.new_context()
            stealth_sync(context)
            page = context.new_page()
            
            try:
                page.goto(url, timeout=60000)
                time.sleep(3)
                
                # Basic heuristics for filling forms
                # 1. Name
                if 'name' in form_data:
                    page.fill("input[name*='name']", form_data['name'])
                    page.fill("input[name*='Name']", form_data['name'])
                
                # 2. Email
                if 'email' in form_data:
                    page.fill("input[name*='email']", form_data['email'])
                    page.fill("input[type='email']", form_data['email'])
                
                # 3. Resume Upload
                if 'resume_path' in form_data:
                    # Look for file inputs
                    file_inputs = page.query_selector_all("input[type='file']")
                    if file_inputs:
                        file_inputs[0].set_input_files(form_data['resume_path'])
                
                # 4. Cover Letter (if applicable)
                # ...
                
                time.sleep(5) 
                
                # We do NOT submit automatically for safety, just fill.
                # The user can review then click submit if running in non-headless mode?
                # For automation, we'd need to identify the submit button.
                
                browser.close()
                return "Form filled (simulated). Please review logic for actual submission."
            except Exception as e:
                browser.close()
                return f"Failed to fill form: {str(e)}"