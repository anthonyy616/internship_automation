from crewai import Agent
from langchain_openai import ChatOpenAI
from src.tools import BrowserTools, JobTools, EmailTools

llm = ChatOpenAI(model="gpt-4o", temperature=0.7)

class JobAgents:
    def researcher(self):
        return Agent(
            role='Senior Tech Recruiter & Sourcing Specialist',
            goal='Identify 10-15 high-quality internship opportunities for 3rd/4th year students.',
            backstory=(
                "You are an elite headhunter with a knack for finding hidden gems. "
                "You know how to use Google Search operators to bypass generic listings and find direct company career pages. "
                "You strictly filter for 'Internship', '2025', and roles suitable for 'Junior' or 'Senior' undergraduates."
            ),
            tools=[BrowserTools.search_jobs],
            llm=llm,
            verbose=True,
            allow_delegation=False
        )

    def scraper(self):
        return Agent(
            role='Talent Operations Analyst',
            goal='Extract precise job details and verify eligibility criteria.',
            backstory=(
                "You are meticulous. You visit every URL found by the Researcher. "
                "You scrape the page to find: 1. Application URL, 2. Hiring Manager Name/Email (if public), 3. Eligibility (Must be 3rd/4th year/Junior/Senior status). "
                "If a job is for 'Freshman' or 'New Grad' (Graduated), you DISCARD it. "
                "You save VALID jobs to the database immediately."
            ),
            tools=[BrowserTools.scrape_website, JobTools.save_job, JobTools.validate_job_text],
            llm=llm,
            verbose=True,
            allow_delegation=False
        )

    def email_writer(self):
        return Agent(
            role='Cold Email Copywriter',
            goal='Craft personalized, high-conversion cold emails.',
            backstory=(
                "You write emails that sound like a precocious, talented student, not a bot. "
                "CRITICAL: You ALWAYS address the recipient by their first name if provided (e.g., 'Hi Sarah'). "
                "If no name is provided, you use 'Hi [Company] Team'. "
                "You avoid formal cliches like 'I hope this email finds you well'. "
                "You get straight to the point: who you are, why you fit, and what you want."
            ),
            tools=[EmailTools.send_email],
            llm=llm,
            verbose=True
        )

    
    def applier(self):
        return Agent(
            role='Application Automation Specialist',
            goal='Submit applications to simplified portals and upload necessary documents.',
            backstory=(
                "You are the closer. You navigate application portals with precision. "
                "You fill out forms using the candidate's profile data and upload the resume. "
                "You report back on success or failure."
            ),
            tools=[BrowserTools.apply_to_job],
            llm=llm,
            verbose=True
        )

    def coordinator(self):
        return Agent(
            role='Career Strategy Coordinator',
            goal='Orchestrate the application process and ensure human review.',
            backstory=(
                "You manage the pipeline. You ensure no duplicate applications are sent. "
                "You present the drafted emails and found jobs to the user for approval before allowing the 'Applier' or 'Sender' to proceed."
            ),
            llm=llm,
            verbose=True,
            allow_delegation=True
        )
