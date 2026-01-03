"""
Multi-region job scraper service.
Scrapes job boards and Google Jobs for internship opportunities.
"""

import asyncio
import random
import re
import urllib.parse
from datetime import datetime
from typing import List, Optional, Dict, Any, Callable
from playwright.async_api import async_playwright, Browser, Page
from bs4 import BeautifulSoup

from backend.models import ScrapedJob, Region


class JobScraperService:
    """
    Scrapes multiple job boards for internship opportunities.
    Uses Playwright with stealth mode to avoid detection.
    """
    
    # Region-specific job board configurations
    JOB_BOARDS = {
        'EU': [
            {
                'name': 'LinkedIn EU',
                'base_url': 'https://www.linkedin.com/jobs/search/',
                'params': 'keywords={query}&location=European%20Union&f_TPR=r604800',
                'selectors': {
                    'job_cards': '.jobs-search__results-list li',
                    'title': '.base-search-card__title',
                    'company': '.base-search-card__subtitle',
                    'link': '.base-card__full-link',
                }
            },
            {
                'name': 'Indeed Germany',
                'base_url': 'https://de.indeed.com/jobs',
                'params': 'q={query}&l=Germany&fromage=7',
                'selectors': {
                    'job_cards': '.job_seen_beacon',
                    'title': '.jobTitle',
                    'company': '.companyName',
                    'link': '.jcs-JobTitle',
                }
            },
        ],
        'UK': [
            {
                'name': 'LinkedIn UK',
                'base_url': 'https://www.linkedin.com/jobs/search/',
                'params': 'keywords={query}&location=United%20Kingdom&f_TPR=r604800',
                'selectors': {
                    'job_cards': '.jobs-search__results-list li',
                    'title': '.base-search-card__title',
                    'company': '.base-search-card__subtitle',
                    'link': '.base-card__full-link',
                }
            },
            {
                'name': 'Indeed UK',
                'base_url': 'https://uk.indeed.com/jobs',
                'params': 'q={query}&fromage=7',
                'selectors': {
                    'job_cards': '.job_seen_beacon',
                    'title': '.jobTitle',
                    'company': '.companyName',
                    'link': '.jcs-JobTitle',
                }
            },
            {
                'name': 'RateMyPlacement',
                'base_url': 'https://www.ratemyplacement.co.uk/search',
                'params': 'show=jobs&keywords={query}',
                'selectors': {
                    'job_cards': '.search-result',
                    'title': '.search-result__title',
                    'company': '.search-result__company',
                    'link': 'a.search-result__link',
                }
            },
        ],
        'Nigeria': [
            {
                'name': 'Jobberman',
                'base_url': 'https://www.jobberman.com/jobs',
                'params': 'q={query}',
                'selectors': {
                    'job_cards': '.job-card',
                    'title': '.job-card__title',
                    'company': '.job-card__company',
                    'link': 'a.job-card__link',
                }
            },
            {
                'name': 'MyJobMag',
                'base_url': 'https://www.myjobmag.com/jobs',
                'params': 'q={query}',
                'selectors': {
                    'job_cards': '.job-list-item',
                    'title': '.job-title',
                    'company': '.company-name',
                    'link': 'a.job-link',
                }
            },
        ],
        'Turkiye': [
            {
                'name': 'LinkedIn Turkey',
                'base_url': 'https://www.linkedin.com/jobs/search/',
                'params': 'keywords={query}&location=Turkey&f_TPR=r604800',
                'selectors': {
                    'job_cards': '.jobs-search__results-list li',
                    'title': '.base-search-card__title',
                    'company': '.base-search-card__subtitle',
                    'link': '.base-card__full-link',
                }
            },
            {
                'name': 'Kariyer.net',
                'base_url': 'https://www.kariyer.net/is-ilanlari',
                'params': 'arama={query}',
                'selectors': {
                    'job_cards': '.job-list-item',
                    'title': '.job-title',
                    'company': '.company-name',
                    'link': 'a.job-link',
                }
            },
        ],
    }
    
    # Eligibility keywords
    POSITIVE_KEYWORDS = [
        'intern', 'internship', 'junior', 'entry level', 'entry-level',
        'graduate', 'student', '2025', '2026', 'summer',
        'undergraduate', 'bachelor', '3rd year', '4th year'
    ]
    
    NEGATIVE_KEYWORDS = [
        'senior engineer', 'lead', 'manager', 'director', '5+ years',
        '7+ years', '10+ years', 'principal', 'staff engineer'
    ]
    
    def __init__(self, log_callback: Optional[Callable] = None):
        self.browser: Optional[Browser] = None
        self.log = log_callback or (lambda *args, **kwargs: None)
    
    async def initialize(self):
        """Initialize the browser."""
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
    
    async def close(self):
        """Close the browser."""
        if self.browser:
            await self.browser.close()
    
    async def _create_stealth_page(self) -> Page:
        """Create a new page with stealth settings."""
        context = await self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
        )
        
        page = await context.new_page()
        
        # Add stealth scripts
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)
        
        return page
    
    async def _random_delay(self, min_sec: float = 1, max_sec: float = 3):
        """Add random delay to mimic human behavior."""
        await asyncio.sleep(random.uniform(min_sec, max_sec))
    
    async def scrape_google_jobs(self, query: str, region: str) -> List[ScrapedJob]:
        """Scrape Google Jobs for the given query and region."""
        jobs = []
        
        location_map = {
            'EU': 'Europe',
            'UK': 'United Kingdom',
            'Nigeria': 'Nigeria',
            'Turkiye': 'Turkey'
        }
        
        location = location_map.get(region, region)
        encoded_query = urllib.parse.quote(f"{query} internship {location}")
        search_url = f"https://www.google.com/search?q={encoded_query}&ibp=htl;jobs"
        
        page = await self._create_stealth_page()
        
        try:
            await self.log('INFO', 'SEARCH', f'Searching Google Jobs: {query}', region)
            
            await self._random_delay(2, 4)
            await page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
            await self._random_delay(2, 3)
            
            # Scroll to load more results
            await page.evaluate('window.scrollTo(0, 500)')
            await self._random_delay(1, 2)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Try to find job listings (Google's structure varies)
            job_elements = soup.select('div[data-ved]')
            
            for elem in job_elements[:10]:  # Limit to 10 results
                try:
                    title_elem = elem.select_one('div[role="heading"]')
                    company_elem = elem.select_one('div.sMzDkb')
                    
                    if title_elem and company_elem:
                        title = title_elem.get_text(strip=True)
                        company = company_elem.get_text(strip=True)
                        
                        if title and company:
                            jobs.append(ScrapedJob(
                                title=title,
                                company=company,
                                url=search_url,
                                region=region,
                                source='Google Jobs',
                                description=''
                            ))
                except Exception:
                    continue
            
            await self.log('SUCCESS', 'SEARCH', f'Found {len(jobs)} jobs on Google Jobs', region)
            
        except Exception as e:
            await self.log('ERROR', 'SEARCH', f'Google Jobs error: {str(e)}', region)
        finally:
            await page.close()
        
        return jobs
    
    async def scrape_job_board(
        self,
        query: str,
        region: str,
        board_config: Dict[str, Any]
    ) -> List[ScrapedJob]:
        """Scrape a specific job board."""
        jobs = []
        board_name = board_config['name']
        
        params = board_config['params'].format(query=urllib.parse.quote(query))
        url = f"{board_config['base_url']}?{params}"
        
        page = await self._create_stealth_page()
        
        try:
            await self.log('INFO', 'SCRAPE', f'Scraping {board_name}', region)
            
            await self._random_delay(2, 4)
            await page.goto(url, timeout=60000, wait_until='domcontentloaded')
            await self._random_delay(2, 3)
            
            # Scroll to load lazy content
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight / 2)')
            await self._random_delay(1, 2)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            selectors = board_config['selectors']
            job_cards = soup.select(selectors['job_cards'])
            
            for card in job_cards[:15]:  # Limit per board
                try:
                    title_elem = card.select_one(selectors['title'])
                    company_elem = card.select_one(selectors['company'])
                    link_elem = card.select_one(selectors['link'])
                    
                    if title_elem and company_elem:
                        title = title_elem.get_text(strip=True)
                        company = company_elem.get_text(strip=True)
                        
                        job_url = url
                        if link_elem and link_elem.get('href'):
                            href = link_elem.get('href')
                            if href.startswith('http'):
                                job_url = href
                            elif href.startswith('/'):
                                base = board_config['base_url'].split('/')[0:3]
                                job_url = '/'.join(base) + href
                        
                        if title and company and self._is_relevant(title):
                            jobs.append(ScrapedJob(
                                title=title,
                                company=company,
                                url=job_url,
                                region=region,
                                source=board_name,
                                description=''
                            ))
                except Exception:
                    continue
            
            await self.log('SUCCESS', 'SCRAPE', f'Found {len(jobs)} jobs on {board_name}', region)
            
        except Exception as e:
            await self.log('ERROR', 'SCRAPE', f'{board_name} error: {str(e)}', region)
        finally:
            await page.close()
        
        return jobs
    
    def _is_relevant(self, title: str) -> bool:
        """Check if job title is relevant (intern/entry level)."""
        title_lower = title.lower()
        
        # Check for negative keywords first
        for neg in self.NEGATIVE_KEYWORDS:
            if neg in title_lower:
                return False
        
        # Check for positive keywords
        for pos in self.POSITIVE_KEYWORDS:
            if pos in title_lower:
                return True
        
        return False
    
    async def scrape_region(self, region: str, keywords: List[str]) -> List[ScrapedJob]:
        """Scrape all job boards for a specific region."""
        all_jobs = []
        boards = self.JOB_BOARDS.get(region, [])
        
        await self.log('INFO', 'SEARCH', f'Starting search in {region} with {len(boards)} job boards', region)
        
        for keyword in keywords[:3]:  # Limit keywords per region
            # Scrape Google Jobs
            google_jobs = await self.scrape_google_jobs(keyword, region)
            all_jobs.extend(google_jobs)
            
            # Scrape each job board
            for board in boards:
                board_jobs = await self.scrape_job_board(keyword, region, board)
                all_jobs.extend(board_jobs)
                await self._random_delay(2, 5)  # Delay between boards
        
        # Deduplicate by URL
        seen_urls = set()
        unique_jobs = []
        for job in all_jobs:
            if job.url not in seen_urls:
                seen_urls.add(job.url)
                unique_jobs.append(job)
        
        await self.log('SUCCESS', 'SEARCH', f'Total unique jobs in {region}: {len(unique_jobs)}', region)
        return unique_jobs
    
    async def scrape_all_regions(
        self,
        regions: List[str],
        keywords: List[str]
    ) -> Dict[str, List[ScrapedJob]]:
        """Scrape all selected regions concurrently."""
        await self.initialize()
        
        results = {}
        
        try:
            # Run regions concurrently
            tasks = [self.scrape_region(region, keywords) for region in regions]
            region_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for region, result in zip(regions, region_results):
                if isinstance(result, Exception):
                    await self.log('ERROR', 'SEARCH', f'Region {region} failed: {str(result)}', region)
                    results[region] = []
                else:
                    results[region] = result
        finally:
            await self.close()
        
        return results
