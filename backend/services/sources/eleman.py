"""
Eleman.net Adapter (Türkiye).

The plan originally named Kariyer.net for Türkiye, but kariyer.net returns
HTTP 403 (anti-bot) to non-browser clients, so eleman.net — one of the
country's largest job boards with server-rendered listings — is used instead.

Job cards live under div.ilan_listeleme_bol on category pages:
  /is-ilanlari/bilisim   (IT)
  /is-ilanlari/muhendis  (engineering)
  /is-ilanlari/tekniker  (technicians)
  /is-ilanlari/teknisyen (technicians)

Titles are in Turkish, so English keywords are mapped to Turkish search
terms before filtering.
"""

import re
import unicodedata
from typing import List

import httpx
from bs4 import BeautifulSoup

from backend.services.sources.base import SourceAdapter, JobListing

# Category pages relevant to an engineering/CS student
CATEGORY_PATHS = ["bilisim", "muhendis", "tekniker", "teknisyen"]

# English -> Turkish term mapping for keyword filtering
TURKISH_TERMS = {
    "intern": ["staj", "stajyer"],
    "internship": ["staj", "stajyer"],
    "junior": ["junior"],
    "graduate": ["yeni mezun"],
    "new grad": ["yeni mezun"],
    "software": ["yazılım", "yazilim", "bilgisayar"],
    "engineer": ["mühendis", "muhendis"],
    "developer": ["geliştirici", "gelistirici", "developer"],
    "data": ["veri"],
    "machine learning": ["yapay zeka", "makine öğrenmesi", "makine ogrenmesi", "ml"],
    "cloud": ["cloud", "bulut"],
    "devops": ["devops"],
    "embedded": ["gömülü", "gomulu"],
    "frontend": ["frontend", "front-end", "ön yüz"],
    "backend": ["backend", "back-end", "arka yüz"],
}

NEGATIVE_TITLE_TOKENS = ["kıdemli", "kidemli", "yönetici", "yonetici", "müdür", "mudur", "direktör", "direktor", "şef", "sef", "uzman", "kıdem"]

# Turkish city names used to split company|location inside the subtitle span
TURKISH_CITIES = [
    "adana", "adiyaman", "afyon", "ağrı", "agri", "amasya", "ankara", "antalya", "artvin",
    "aydın", "aydin", "balıkesir", "balikesir", "bilecik", "bingöl", "bingol", "bitlis",
    "bolu", "burdur", "bursa", "çanakkale", "canakkale", "çankırı", "cankiri", "çorum",
    "corum", "denizli", "diyarbakır", "diyarbakir", "edirne", "elazığ", "elazig", "erzincan",
    "erzurum", "eskişehir", "eskisehir", "gaziantep", "giresun", "gümüşhane", "gumushane",
    "hakkari", "hatay", "ığdır", "igdir", "ısparta", "isparta", "istanbul", "izmir",
    "kahramanmaraş", "kahramanmaras", "karabük", "karabuk", "karaman", "kars", "kastamonu",
    "kayseri", "kırıkkale", "kirikkale", "kırklareli", "kirklareli", "kırşehir", "kirsehir",
    "kilis", "kocaeli", "konya", "kütahya", "kutahya", "malatya", "manisa", "mardin",
    "mersin", "muğla", "mugla", "muş", "mus", "nevşehir", "nevsehir", "niğde", "nigde",
    "ordu", "osmaniye", "rize", "sakarya", "samsun", "siirt", "sinop", "sivas", "şanlıurfa",
    "sanliurfa", "şırnak", "sirnak", "tekirdağ", "tekirdag", "tokat", "trabzon", "tunceli",
    "uşak", "usak", "van", "yalova", "yozgat", "zonguldak", "lefkoşa", "gazimağusa", "girne",
    "kktc", "trnc", "istanbul anadolu", "istanbul avrupa",
]

def _normalize(text: str) -> str:
    """Lowercase and fold Turkish diacritics for robust matching.

    Handles the Turkish capital İ (U+0130), which lowercases to 'i' plus a
    combining dot — plain 'istanbul' must still match 'İstanbul'.
    """
    text = text.lower()
    folded = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(ch)
    )
    return (folded.replace("ş", "s").replace("ğ", "g").replace("ü", "u")
                  .replace("ö", "o").replace("ç", "c"))


def _keyword_terms(keywords: List[str]) -> List[str]:
    """Map English keywords to the Turkish terms used in job titles."""
    terms: List[str] = []
    for kw in keywords:
        kw_lower = _normalize(kw)
        mapped = False
        for eng, turkish in TURKISH_TERMS.items():
            if eng in kw_lower:
                terms.extend(turkish)
                mapped = True
        if not mapped:
            terms.append(kw_lower)
    return list(dict.fromkeys(terms))


def _split_company_location(subtitle: str) -> tuple:
    """Best-effort split of '<Company><City>' inside the subtitle span."""
    subtitle = subtitle.strip()
    norm = _normalize(subtitle)
    # Find the last city name in the normalized string
    best_pos = -1
    best_len = 0
    for city in TURKISH_CITIES:
        idx = norm.rfind(city)
        if idx != -1 and idx > best_pos:
            best_pos = idx
            best_len = len(city)
    if best_pos > 0:
        company = subtitle[:best_pos].strip(" -–")
        location = subtitle[best_pos:].strip(" -–")
        return company, location
    return subtitle, "Türkiye"


class ElemanAdapter(SourceAdapter):
    """Eleman.net Türkiye — HTML scrape adapter."""

    BASE = "https://www.eleman.net"
    LISTINGS_URL = "https://www.eleman.net/is-ilanlari"

    @property
    def name(self) -> str:
        return "eleman"

    @property
    def source_type(self) -> str:
        return "scrape"

    @property
    def base_url(self) -> str:
        return self.BASE

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs: List[JobListing] = []
        seen_slugs = set()
        terms = _keyword_terms(keywords) or ["staj", "yeni mezun", "junior"]

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
        ) as client:
            for category in CATEGORY_PATHS:
                try:
                    response = await client.get(f"{self.LISTINGS_URL}/{category}")
                    if response.status_code != 200:
                        continue

                    soup = BeautifulSoup(response.text, "html.parser")

                    for card in soup.select("div.ilan_listeleme_bol"):
                        a = card.select_one('a[href*="/is-ilani/"]')
                        if not a:
                            continue

                        href = a.get("href", "")
                        slug = href.rstrip("/").split("/")[-1]
                        if not slug or slug in seen_slugs:
                            continue

                        title_el = card.select_one("h3.c-showcase-box__title") or card.select_one("h3")
                        title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
                        if not title:
                            continue

                        title_lower = _normalize(title)

                        # Seniority filter
                        if any(neg in title_lower for neg in NEGATIVE_TITLE_TOKENS):
                            continue

                        # Relevance filter — must contain at least one mapped term
                        if not any(term in title_lower for term in terms):
                            continue

                        # Company + location from the subtitle span
                        subtitle_el = card.select_one("span.c-showcase-box__subtitle")
                        company, location = "", ""
                        if subtitle_el:
                            company, location = _split_company_location(subtitle_el.get_text(strip=True))

                        seen_slugs.add(slug)
                        if href.startswith("http"):
                            url = href
                        else:
                            url = f"{self.BASE}{href if href.startswith('/') else '/' + href}"
                        jobs.append(JobListing(
                            source=self.name,
                            external_id=slug,
                            title=title,
                            company=company,
                            url=url,
                            description="",
                            region=region,
                            location=location,
                            tags=["Türkiye"],
                        ))

                except Exception:
                    continue

        return jobs

    async def health_check(self) -> bool:
        """Verify a category page is reachable and contains job cards."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(f"{self.LISTINGS_URL}/bilisim")
                return response.status_code == 200 and "ilan_listeleme" in response.text
        except Exception:
            return False