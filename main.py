import yaml
import os
import sys
from datetime import datetime

# Add the current directory to sys.path to ensure local imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.agents import JobAgents
from src.tools import JobTools
from crewai import Task, Crew, Process

# Load config
try:
    with open('config/config.yaml', 'r') as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print("Config file not found. Please ensure config/config.yaml exists.")
    sys.exit(1)

# Initialize Agents
agents = JobAgents()
researcher = agents.researcher()
scraper = agents.scraper()
email_writer = agents.email_writer()
applier = agents.applier()
coordinator = agents.coordinator()

def run_internship_search_flow():
    print(f"Starting Internship Search for: {config['user_profile']['name']}")
    
    # 1. Research Task
    search_keywords = ", ".join(config['search_criteria']['keywords'][:3]) # Use top 3 keywords
    research_task = Task(
        description=f"""
            Search for 5 promising internship postings for summer 2025.
            Keywords: {search_keywords}.
            Location: {', '.join(config['search_criteria']['locations'])}.
            Must be suitable for a {config['user_profile']['university_year']} Student in {config['user_profile']['major']}.
            Return a list of URLs.
        """,
        expected_output="A list of 5 valid URLs found.",
        agent=researcher
    )

    # 2. Scrape & Filter Task
    scrape_task = Task(
        description="""
            Visit each found URL.
            Extract: Company, Job Title, Apply URL, Contact Email (if Any).
            Verify constraints: Must be for students (not new grads), 2025 start.
            Save VALID jobs to the database using the 'Save Job' tool.
            Output a JSON summary of saved jobs.
        """,
        expected_output="JSON list of valid, saved jobs.",
        agent=scraper,
        context=[research_task] # Depends on research output
    )

    # Crew 1: Finding Jobs
    finder_crew = Crew(
        agents=[researcher, scraper],
        tasks=[research_task, scrape_task],
        verbose=True,
        process=Process.sequential
    )
    
    print("\n--- PHASE 1: FINDING & FILTERING JOBS ---\n")
    finder_result = finder_crew.kickoff()
    print("\n--- FOUND JOBS ---\n", finder_result)
    
    # In a real loop, we would read from DB here to process 'found' jobs.
    # For this prototype, we'll ask user which one to apply to from the result, 
    # or just proceed with the last context.
    
    
    print("\n--- PHASE 2: HUMAN APPROVAL ---\n")
    
    # In a real scenario, we'd fetch the most recent 'found' job from DB.
    # For this prototype, we'll verify if we have data passed from the crawler.
    # We will simulate fetching the "best" result for the demo.
    
    # Mocking data passing if agent output is just text. 
    # ideally Scraper outputs JSON structure. 
    # Let's assume we selected one job:
    selected_job = {
        "company": "Tech Corp", 
        "title": "Software Intern", 
        "manager_name": "Sarah Jones", # Scraper should find this
        "description": "Work on distributed systems and python."
    }
    
    proceed = input("Do you want to proceed with generating emails/applications for saved jobs? (y/n): ")
    if proceed.lower() != 'y':
        print("Stopping here.")
        return

    # Load Template
    with open('templates/email_template.txt', 'r') as t:
        template_text = t.read()

    # 3. Outreach Task (Email)
    # We explicitly instruct the LLM to use the template and fill in the blanks
    email_task = Task(
        description=f"""
            Draft a cold email for the following job using the provided template.
            
            JOB DETAILS:
            Company: {selected_job['company']}
            Title: {selected_job['title']}
            Hiring Manager: {selected_job['manager_name']} (If "None" or "Unknown", use "Hiring Team")
            Context: {selected_job['description']}
            
            CANDIDATE DETAILS:
            Name: {config['user_profile']['name']}
            Year: {config['user_profile']['university_year']}
            Major: {config['user_profile']['major']}
            Skills: {', '.join(config['user_profile']['skills'])}
            
            TEMPLATE:
            {template_text}
            
            INSTRUCTIONS:
            - Replace all {{placeholders}} with real data.
            - If Hiring Manager is known, address them by first name (e.g. "Hi Sarah").
            - If unknown, use "Hi {selected_job['company']} Team".
            - The "Personal Hook" should be generated based on the Job Description + Candidate Skills.
            - Keep it human, casual but professional.
        """,
        expected_output="Final Email Subject and Body.",
        agent=email_writer
    )
    
    # Crew 2: Action - DRAFTING
    # We deliberately split Drafting and Sending for HITL safety.
    email_crew = Crew(
        agents=[email_writer],
        tasks=[email_task],
        verbose=True
    )
    
    email_content = email_crew.kickoff()
    
    print("\n--- DRAFTED EMAIL ---\n")
    print(email_content)
    
    # HITL for Sending
    send = input("\n[ACTION REQUIRED] Send this email? (y/n): ")
    if send.lower() == 'y':
        # We can now use the Agent to send it, OR just use the tool directly.
        # Using the agent is more "agentic" but might be overkill for a single action.
        # Let's use the tool class directly here for simplicity and reliability in the main script,
        # OR define a "Sending Task". Let's use the Tool directly to avoid another Agent spin-up cost.
        from src.tools import EmailTools
        
        # We need to extract the subject/body from the agent output. 
        # For now, we'll assume the agent output IS the body and we use a generic subject 
        # or ask the agent to output JSON.
        # To be safe:
        res = EmailTools.send_email(
            recipient="recruiter@example.com", # In real app, extract from job data
            subject=f"Internship Inquiry - {config['user_profile']['name']}",
            body=str(email_content)
        )
        print(res)

    # 4. Apply Task
    # Check if there is a 'quick apply' url
    # ...

def main_loop():
    daily_limit = config['safety']['max_actions_per_day']
    actions_count = 0
    
    while True:
        if actions_count >= daily_limit:
            print("Daily safety limit reached. Sleeping until tomorrow.")
            break
            
        print(f"\n--- SESSION START (Action {actions_count + 1}/{daily_limit}) ---")
        run_internship_search_flow()
        actions_count += 1
        
        cont = input("\nDo you want to start a new search? (y/n): ")
        if cont.lower() != 'y':
            print("Exiting Bot. Goodbye!")
            break

if __name__ == "__main__":
    main_loop()
