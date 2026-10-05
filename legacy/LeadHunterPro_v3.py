"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║         L E A D H U N T E R   P R O   v3.0                         ║
║         Premium Local Business Intelligence                          ║
║         by AunSEO                                                    ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# ── Auto-install packages ─────────────────────────────────────────────
import subprocess, sys

REQUIRED = {
    "customtkinter":    "customtkinter",
    "selenium":         "selenium",
    "webdriver_manager":"webdriver-manager",
    "pandas":           "pandas",
    "requests":         "requests",
    "bs4":              "beautifulsoup4",
    "openpyxl":         "openpyxl",
    "Pillow":           "Pillow",
    "dns":              "dnspython",
    "folium":           "folium",
}
for imp, pkg in REQUIRED.items():
    try:    __import__(imp)
    except ImportError:
        print(f"[SETUP] Installing {pkg}...")
        subprocess.check_call([sys.executable,"-m","pip","install",pkg,"-q",
                               "--disable-pip-version-check"])

# ── Standard imports ──────────────────────────────────────────────────
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import threading, time, re, os, csv, json, random, warnings
import datetime, socket, struct, webbrowser, tempfile, math
import concurrent.futures
from urllib.parse import urljoin, urlparse, quote_plus
from collections import deque
import requests as rq
from bs4 import BeautifulSoup
import pandas as pd
import dns.resolver

warnings.filterwarnings("ignore")

# ── Selenium (lazy import) ────────────────────────────────────────────
_selenium_ok = False
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    _selenium_ok = True
except: pass

# ═════════════════════════════════════════════════════════════════════
#  THEME
# ═════════════════════════════════════════════════════════════════════
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "bg":         "#0A0F1A",
    "bg2":        "#0F1623",
    "bg3":        "#141D2E",
    "bg4":        "#1A2540",
    "sidebar":    "#0D1320",
    "card":       "#111827",
    "border":     "#1E2D45",
    "accent":     "#0EA5E9",
    "accent2":    "#0284C7",
    "green":      "#10B981",
    "yellow":     "#F59E0B",
    "red":        "#EF4444",
    "purple":     "#8B5CF6",
    "orange":     "#F97316",
    "text":       "#F1F5F9",
    "text2":      "#94A3B8",
    "text3":      "#475569",
    "hot":        "#EF4444",
    "warm":       "#F59E0B",
    "cold":       "#94A3B8",
    "dominant":   "#8B5CF6",
}

# ═════════════════════════════════════════════════════════════════════
#  COUNTRIES & NICHES DATA
# ═════════════════════════════════════════════════════════════════════
COUNTRIES = [
    "Pakistan","Australia","United States","United Kingdom",
    "Canada","UAE","Saudi Arabia","India","Germany","France",
    "New Zealand","Singapore","Malaysia","South Africa",
    "Nigeria","Philippines","Ireland","Netherlands",
    "Sweden","Norway","Denmark","Switzerland","Italy","Spain",
]

COUNTRY_CITIES = {
    "Pakistan": ["Karachi","Lahore","Islamabad","Rawalpindi","Faisalabad","Multan",
        "Peshawar","Quetta","Sialkot","Gujranwala","Hyderabad","Bahawalpur",
        "Sargodha","Sukkur","Larkana","Sheikhupura","Rahim Yar Khan","Jhang",
        "Dera Ghazi Khan","Gujrat","Sahiwal","Mardan","Mingora","Abbottabad","Swabi"],
    "Australia": ["Sydney","Melbourne","Brisbane","Perth","Adelaide","Gold Coast",
        "Newcastle","Canberra","Wollongong","Geelong","Hobart","Townsville",
        "Cairns","Darwin","Toowoomba","Ballarat","Bendigo","Launceston",
        "Mackay","Rockhampton","Bundaberg","Sunshine Coast","Central Coast","Mandurah"],
    "United States": ["New York","Los Angeles","Chicago","Houston","Phoenix",
        "Philadelphia","San Antonio","San Diego","Dallas","Austin","Jacksonville",
        "Fort Worth","Columbus","Charlotte","Indianapolis","San Francisco","Seattle",
        "Denver","Nashville","Oklahoma City","Boston","Memphis","Louisville","Portland",
        "Baltimore","Milwaukee","Albuquerque","Tucson","Fresno","Sacramento",
        "Mesa","Kansas City","Atlanta","Miami","Cleveland","Pittsburgh","Tampa",
        "Minneapolis","New Orleans","Raleigh","Arlington","Wichita","Bakersfield"],
    "United Kingdom": ["London","Birmingham","Manchester","Leeds","Glasgow","Sheffield",
        "Bradford","Edinburgh","Liverpool","Bristol","Cardiff","Coventry","Leicester",
        "Nottingham","Newcastle","Belfast","Southampton","Brighton","Hull","Plymouth",
        "Stoke-on-Trent","Wolverhampton","Derby","Swansea","Aberdeen","Oxford",
        "Cambridge","Norwich","Exeter","Bath","York","Portsmouth"],
    "Canada": ["Toronto","Montreal","Vancouver","Calgary","Edmonton","Ottawa",
        "Winnipeg","Quebec City","Hamilton","Kitchener","London","Victoria",
        "Halifax","Windsor","Saskatoon","Regina","Markham","Vaughan","Gatineau",
        "Burnaby","Surrey","Laval","Brampton","Mississauga","Kelowna","Barrie"],
    "UAE": ["Dubai","Abu Dhabi","Sharjah","Ajman","Ras Al Khaimah",
        "Fujairah","Umm Al Quwain","Al Ain","Khor Fakkan","Jebel Ali","Deira"],
    "Saudi Arabia": ["Riyadh","Jeddah","Mecca","Medina","Dammam","Khobar","Tabuk",
        "Buraidah","Khamis Mushait","Hail","Najran","Jizan","Yanbu","Taif","Abha"],
    "India": ["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Kolkata","Pune",
        "Ahmedabad","Jaipur","Surat","Lucknow","Kanpur","Nagpur","Indore","Bhopal",
        "Patna","Vadodara","Ludhiana","Agra","Nashik","Faridabad","Meerut","Rajkot",
        "Varanasi","Srinagar","Aurangabad","Amritsar","Coimbatore","Gwalior","Vijayawada"],
    "Germany": ["Berlin","Hamburg","Munich","Cologne","Frankfurt","Stuttgart",
        "Dusseldorf","Leipzig","Dortmund","Essen","Bremen","Dresden","Hanover",
        "Nuremberg","Duisburg","Bochum","Wuppertal","Bielefeld","Bonn","Mannheim",
        "Karlsruhe","Wiesbaden","Munster","Augsburg","Aachen","Kiel","Chemnitz"],
    "France": ["Paris","Lyon","Marseille","Toulouse","Nice","Nantes","Montpellier",
        "Strasbourg","Bordeaux","Lille","Rennes","Reims","Saint-Etienne","Toulon",
        "Le Havre","Grenoble","Dijon","Angers","Nimes","Clermont-Ferrand","Brest"],
    "New Zealand": ["Auckland","Wellington","Christchurch","Hamilton","Tauranga",
        "Napier","Palmerston North","Nelson","Rotorua","New Plymouth","Whangarei",
        "Invercargill","Dunedin","Gisborne","Blenheim"],
    "Singapore": ["Singapore","Jurong","Tampines","Woodlands","Ang Mo Kio","Bedok"],
    "Malaysia": ["Kuala Lumpur","Penang","Johor Bahru","Ipoh","Shah Alam",
        "Petaling Jaya","Kota Kinabalu","Kuching","Malacca","Subang Jaya","Klang",
        "Seremban","Miri","Alor Setar","Sibu"],
    "South Africa": ["Johannesburg","Cape Town","Durban","Pretoria","Port Elizabeth",
        "Bloemfontein","East London","Polokwane","Kimberley","Pietermaritzburg",
        "Rustenburg","Soweto","Sandton","Benoni"],
    "Nigeria": ["Lagos","Abuja","Kano","Ibadan","Port Harcourt","Benin City",
        "Maiduguri","Zaria","Aba","Ilorin","Jos","Enugu","Kaduna","Warri",
        "Abeokuta","Onitsha","Uyo","Sokoto","Calabar"],
    "Philippines": ["Manila","Quezon City","Davao","Caloocan","Cebu City","Zamboanga",
        "Taguig","Antipolo","Pasig","Cagayan de Oro","Paranaque","Las Pinas",
        "Makati","Bacolod","Muntinlupa","Iloilo City","Marikina"],
    "Ireland": ["Dublin","Cork","Limerick","Galway","Waterford","Drogheda",
        "Dundalk","Swords","Bray","Kilkenny","Ennis","Tralee","Sligo","Naas"],
    "Netherlands": ["Amsterdam","Rotterdam","The Hague","Utrecht","Eindhoven",
        "Tilburg","Groningen","Almere","Breda","Nijmegen","Haarlem","Enschede",
        "Arnhem","Amersfoort","Zwolle","Maastricht"],
    "Sweden": ["Stockholm","Gothenburg","Malmo","Uppsala","Vasteras","Orebro",
        "Linkoping","Helsingborg","Jonkoping","Norrkoping","Lund","Umea",
        "Gavle","Boras","Sundsvall","Eskilstuna","Karlstad"],
    "Norway": ["Oslo","Bergen","Stavanger","Trondheim","Drammen","Fredrikstad",
        "Kristiansand","Sandnes","Tromso","Sarpsborg","Skien","Bodo"],
    "Denmark": ["Copenhagen","Aarhus","Odense","Aalborg","Esbjerg","Horsens",
        "Randers","Kolding","Vejle","Herning","Silkeborg"],
    "Switzerland": ["Zurich","Geneva","Basel","Lausanne","Bern","Winterthur",
        "Lucerne","St. Gallen","Lugano","Biel","Thun"],
    "Italy": ["Rome","Milan","Naples","Turin","Palermo","Genoa","Bologna",
        "Florence","Bari","Catania","Venice","Verona","Messina","Padua",
        "Trieste","Brescia","Taranto","Prato","Modena"],
    "Spain": ["Madrid","Barcelona","Valencia","Seville","Zaragoza","Malaga",
        "Murcia","Palma","Las Palmas","Bilbao","Alicante","Cordoba",
        "Valladolid","Vigo","Gijon","Granada","Elche","Terrassa"],
}

NICHES = [
    "dental clinic","dentist","orthodontist","dental implants",
    "plumber","plumbing services","emergency plumber",
    "restaurant","cafe","coffee shop","bakery","pizza shop",
    "lawyer","attorney","law firm","divorce lawyer","immigration lawyer",
    "hair salon","barbershop","beauty salon","nail salon","spa",
    "gym","fitness center","yoga studio","personal trainer","CrossFit",
    "real estate agent","property dealer","mortgage broker",
    "auto mechanic","car repair","auto body shop","tyre shop",
    "cleaning service","house cleaning","commercial cleaning",
    "electrician","electrical contractor","solar installer",
    "roofing contractor","roofer","guttering",
    "HVAC","air conditioning","heating repair",
    "pest control","termite inspection",
    "landscaping","lawn care","tree service",
    "photographer","videographer","wedding photographer",
    "accountant","tax consultant","bookkeeper","financial advisor",
    "insurance agent","life insurance","car insurance",
    "veterinarian","pet grooming","dog training",
    "physiotherapist","chiropractor","massage therapist",
    "tutoring center","driving school","music school",
    "hotel","motel","bed and breakfast","holiday rental",
    "digital marketing agency","SEO agency","social media agency",
    "web design","app developer","IT support",
    "printing shop","sign maker","promotional products",
    "florist","event planner","wedding planner",
    "jeweler","watch repair","pawnshop",
    "optometrist","eye clinic","hearing clinic",
    "pharmacy","medical clinic","urgent care",
    "childcare","daycare","kindergarten",
    "funeral home","cremation services",
    "swimming pool builder","pool cleaning",
    "security company","locksmith",
    "moving company","storage facility",
]

# ═════════════════════════════════════════════════════════════════════
#  UTILITY: City Auto-Fetch via OpenStreetMap (FREE)
# ═════════════════════════════════════════════════════════════════════
def fetch_cities(country: str, query: str = "") -> list:
    """Returns cities for a country. Uses hardcoded list first (instant),
    then tries online API for smaller/unlisted countries."""
    # Hardcoded list — always works instantly
    if country in COUNTRY_CITIES:
        cities = COUNTRY_CITIES[country]
        if query:
            cities = [c for c in cities if query.lower() in c.lower()]
        return cities

    # For countries not in hardcoded list, try OSM Nominatim
    try:
        search_q = f"city {country}"
        r = rq.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": search_q, "format": "json", "limit": 40,
                    "addressdetails": 1, "featuretype": "city"},
            headers={"User-Agent": "LeadHunterPro/3.0"},
            timeout=6
        )
        if r.status_code == 200:
            items = r.json()
            cities = []
            for item in items:
                addr = item.get("address", {})
                name = (addr.get("city") or addr.get("town") or
                        addr.get("village") or
                        item.get("display_name","").split(",")[0])
                if name and name not in cities and len(name) > 2:
                    cities.append(name)
            if cities:
                return cities
    except: pass

    return [country]  # fallback: return country name itself

# ═════════════════════════════════════════════════════════════════════
#  UTILITY: Email Verifier (Free — MX + Syntax)
# ═════════════════════════════════════════════════════════════════════
EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}$')
DISPOSABLE_DOMAINS = {
    "mailinator.com","guerrillamail.com","10minutemail.com","tempmail.com",
    "throwam.com","yopmail.com","trashmail.com","fakeinbox.com","maildrop.cc",
}

def verify_email(email: str) -> dict:
    """
    Returns: {valid, score, reason}
    score: 0-100
    """
    if not email or not isinstance(email, str):
        return {"valid": False, "score": 0, "reason": "Empty"}

    email = email.strip().lower()

    # Syntax
    if not EMAIL_RE.match(email):
        return {"valid": False, "score": 0, "reason": "Invalid syntax"}

    domain = email.split("@")[1]

    # Disposable
    if domain in DISPOSABLE_DOMAINS:
        return {"valid": False, "score": 10, "reason": "Disposable email"}

    # MX Record check
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        if answers:
            return {"valid": True, "score": 90, "reason": "MX record found ✅"}
    except dns.resolver.NXDOMAIN:
        return {"valid": False, "score": 0, "reason": "Domain doesn't exist"}
    except dns.resolver.NoAnswer:
        # Try A record as fallback
        try:
            dns.resolver.resolve(domain, "A", lifetime=5)
            return {"valid": True, "score": 60, "reason": "Domain exists (no MX)"}
        except:
            return {"valid": False, "score": 5, "reason": "No mail server found"}
    except:
        return {"valid": True, "score": 50, "reason": "Could not verify (assume valid)"}

    return {"valid": True, "score": 70, "reason": "Likely valid"}

# ═════════════════════════════════════════════════════════════════════
#  UTILITY: Domain Age via RDAP (Free)
# ═════════════════════════════════════════════════════════════════════
def _parse_domain(url: str) -> str:
    """Extract clean domain from URL."""
    if not url: return ""
    if not url.startswith("http"): url = "https://" + url
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    domain = domain.replace("www.","").split("/")[0].split("?")[0].strip()
    # Remove port if present
    if ":" in domain: domain = domain.split(":")[0]
    return domain.lower()

def get_domain_age(url: str) -> dict:
    """
    Get domain age via multiple free methods:
    1. RDAP.org  (primary — works for most TLDs)
    2. IANA RDAP bootstrap (finds correct server per TLD)
    3. who.is scrape (fallback — no API key needed)
    4. whoisjsonapi.com (free tier, no key)
    Returns: {age_days, age_label, created, registrar, error}
    """
    empty = {"age_days": None, "age_label": "Unknown",
             "created": None, "registrar": "", "error": ""}

    domain = _parse_domain(url)
    if not domain or len(domain) < 4:
        return {**empty, "error": "Invalid domain"}

    hdrs = {"User-Agent": "LeadHunterPro/3.0",
            "Accept": "application/json"}

    def _age_label(days):
        if days is None: return "Unknown"
        if days < 90:    return "🆕 < 3 months"
        if days < 365:   return "🆕 < 1 year"
        if days < 730:   return "📅 1-2 years"
        if days < 1825:  return "📅 2-5 years"
        return "🏛️ 5+ years"

    def _parse_rdap_data(data):
        events = data.get("events", [])
        created_date = None
        for ev in events:
            action = ev.get("eventAction","").lower()
            if action in ("registration", "registered"):
                date_str = ev.get("eventDate","")
                if date_str:
                    try:
                        created_date = datetime.datetime.fromisoformat(
                            date_str[:10].replace("Z",""))
                    except: pass
                    break
        if not created_date:
            return None, None, ""

        age = (datetime.datetime.now() - created_date).days
        registrar = ""
        for entity in data.get("entities", []):
            if "registrar" in entity.get("roles", []):
                vc = entity.get("vcardArray", [[],[]])[1]
                for v in vc:
                    if isinstance(v, list) and len(v) >= 4 and v[0] == "fn":
                        registrar = str(v[3])[:60]
                        break
        return age, created_date.strftime("%Y-%m-%d"), registrar

    def _build_result(age, created, registrar):
        return {"age_days": age, "age_label": _age_label(age),
                "created": created, "registrar": registrar, "error": ""}

    # ── Method 1: rdap.org (handles most TLDs including .pk, .co.uk) ──
    for rdap_url in [
        f"https://rdap.org/domain/{domain}",
        f"https://rdap.verisign.com/com/v1/domain/{domain}",
        f"https://rdap.afilias.net/rdap/domain/{domain}",
    ]:
        try:
            r = rq.get(rdap_url, headers=hdrs, timeout=8, verify=False)
            if r.status_code == 200:
                age, created, registrar = _parse_rdap_data(r.json())
                if age is not None:
                    return _build_result(age, created, registrar)
        except: continue

    # ── Method 2: IANA RDAP Bootstrap (correct server per TLD) ────
    try:
        boot = rq.get("https://data.iana.org/rdap/dns.json",
                      timeout=6, headers=hdrs)
        if boot.status_code == 200:
            tld = domain.rsplit(".", 1)[-1].lower()
            for service in boot.json().get("services", []):
                if tld in service[0]:
                    rdap_base = service[1][0].rstrip("/")
                    r = rq.get(f"{rdap_base}/domain/{domain}",
                               headers=hdrs, timeout=8, verify=False)
                    if r.status_code == 200:
                        age, created, registrar = _parse_rdap_data(r.json())
                        if age is not None:
                            return _build_result(age, created, registrar)
                    break
    except: pass

    # ── Method 3: whoisjsonapi.com (free, no API key needed) ───────
    try:
        r = rq.get(
            f"https://whoisjsonapi.com/v1/{domain}",
            headers={**hdrs, "Accept": "application/json"},
            timeout=8
        )
        if r.status_code == 200:
            d = r.json()
            created_str = (d.get("domain", {}).get("created_date") or
                          d.get("created_date") or
                          d.get("creation_date",""))
            if created_str:
                for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d",
                            "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"]:
                    try:
                        dt = datetime.datetime.strptime(created_str[:19], fmt)
                        age = (datetime.datetime.now() - dt).days
                        reg = (d.get("registrar",{}).get("name","")
                               if isinstance(d.get("registrar"), dict)
                               else str(d.get("registrar","")))
                        return _build_result(age, dt.strftime("%Y-%m-%d"), reg[:60])
                    except: continue
    except: pass

    # ── Method 4: who.is HTML scrape (last resort) ──────────────────
    try:
        r = rq.get(
            f"https://who.is/whois/{domain}",
            headers={"User-Agent": random.choice(_UA_POOL),
                     "Accept": "text/html"},
            timeout=10
        )
        if r.status_code == 200:
            # Look for "Creation Date" or "Registered On" in plain text
            text = BeautifulSoup(r.text, "html.parser").get_text()
            for pat in [
                r'Creation\s+Date[:\s]+(\d{4}-\d{2}-\d{2})',
                r'Registered\s+On[:\s]+(\d{4}-\d{2}-\d{2})',
                r'Created[:\s]+(\d{4}-\d{2}-\d{2})',
                r'Domain\s+Created[:\s]+(\d{2}/\d{2}/\d{4})',
            ]:
                m = re.search(pat, text, re.I)
                if m:
                    date_str = m.group(1)
                    for fmt in ["%Y-%m-%d", "%d/%m/%Y"]:
                        try:
                            dt = datetime.datetime.strptime(date_str, fmt)
                            age = (datetime.datetime.now() - dt).days
                            return _build_result(age, dt.strftime("%Y-%m-%d"), "")
                        except: continue
    except: pass

    return {**empty, "error": f"Could not fetch WHOIS for {domain}"}

# ═════════════════════════════════════════════════════════════════════
#  UTILITY: GMB Completeness Score
# ═════════════════════════════════════════════════════════════════════
def gmb_completeness(biz: dict) -> dict:
    """Score how complete a Google Business Profile is (0-8)"""
    checks = {
        "Has Website":      bool(biz.get("website","")),
        "Has Phone":        bool(biz.get("phone","")),
        "Has Address":      bool(biz.get("address","")),
        "Has Rating":       bool(biz.get("rating","")),
        "Has Reviews":      int(biz.get("reviews",0) or 0) > 0,
        "Has Category":     bool(biz.get("category","")),
        "Has Hours":        bool(biz.get("hours","")),
        "10+ Reviews":      int(biz.get("reviews",0) or 0) >= 10,
    }
    score = sum(1 for v in checks.values() if v)
    pct   = int((score / 8) * 100)
    if pct >= 87:   grade = "Complete"
    elif pct >= 62: grade = "Good"
    elif pct >= 37: grade = "Incomplete"
    else:           grade = "Very Incomplete"

    return {"score": score, "max": 8, "pct": pct,
            "grade": grade, "checks": checks}

# ═════════════════════════════════════════════════════════════════════
#  UTILITY: Lead & Opportunity Scoring
# ═════════════════════════════════════════════════════════════════════
def score_lead(biz: dict, position: int = 0) -> dict:
    """
    Lead Score  0-100: How easy to sell to
    Opportunity 0-100: How much they need SEO services
    Tag: HOT / WARM / COLD / DOMINANT
    """
    try: reviews = int(biz.get("reviews", 0) or 0)
    except: reviews = 0
    try: rating  = float(biz.get("rating", 0) or 0)
    except: rating = 0.0

    has_website  = bool(biz.get("website",""))
    has_phone    = bool(biz.get("phone",""))
    has_email    = bool(biz.get("email",""))
    has_hours    = bool(biz.get("hours",""))

    gmb = gmb_completeness(biz)
    gmb_pct = gmb["pct"]

    # ── Lead Score (ease of selling) ────────────────────────
    lead = 0

    # No website = huge opportunity
    if not has_website:       lead += 30
    elif has_website:         lead += 5

    # Low reviews = new/struggling
    if reviews == 0:          lead += 25
    elif reviews <= 5:        lead += 20
    elif reviews <= 15:       lead += 15
    elif reviews <= 30:       lead += 8
    else:                     lead += 0

    # Low position in results
    if position > 15:         lead += 15
    elif position > 10:       lead += 10
    elif position > 5:        lead += 5

    # Incomplete GMB
    if gmb_pct < 40:          lead += 15
    elif gmb_pct < 60:        lead += 10
    elif gmb_pct < 80:        lead += 5

    # Has contact info = reachable
    if has_email:             lead += 10
    if has_phone:             lead += 5

    # Low rating = needs help
    if 0 < rating < 3.5:      lead += 10
    elif rating < 4.0:        lead += 5

    lead = min(100, lead)

    # ── Opportunity Score ────────────────────────────────────
    opp = 0
    if not has_website:       opp += 30
    if reviews < 10:          opp += 25
    if gmb_pct < 50:          opp += 20
    if not has_hours:         opp += 10
    if position > 10:         opp += 15
    opp = min(100, opp)

    # ── Tag ──────────────────────────────────────────────────
    if reviews > 100 and position <= 3 and gmb_pct >= 75:
        tag = "DOMINANT"
    elif lead >= 70:
        tag = "HOT LEAD 🔥"
    elif lead >= 45:
        tag = "WARM LEAD ♨️"
    else:
        tag = "COLD"

    return {
        "lead_score":  lead,
        "opp_score":   opp,
        "tag":         tag,
        "gmb_score":   f"{gmb['score']}/8",
        "gmb_grade":   gmb["grade"],
    }

# ═════════════════════════════════════════════════════════════════════
#  UTILITY: Email extractor from website
# ═════════════════════════════════════════════════════════════════════
# ── Email regex — strict but catches real emails ──────────────────────────
# local part: 1-64 chars, must have at least one letter, no leading/trailing dot
# domain: valid TLD 2-10 alpha chars
_EMAIL_FIND_RE = re.compile(
    r'\b[A-Za-z0-9][A-Za-z0-9._%+\-]{0,62}@[A-Za-z0-9][A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,10}\b'
)

# Domains that are never real contact emails
_BAD_EMAIL_D = {
    "example.com","domain.com","sentry.io","wixpress.com","schema.org",
    "googleapis.com","w3.org","yourdomain.com","test.com","email.com",
    "mailchimp.com","sendgrid.net","amazonses.com","sparkpostmail.com",
    "mandrillapp.com","mailgun.org","exacttarget.com","klaviyo.com",
    "brevo.com","constantcontact.com","hubspot.com","marketo.com",
    "pardot.com","salesforce.com","zendesk.com","intercom.io",
    "wordpress.com","shopify.com","squarespace.com","wix.com",
}
_BAD_EMAIL_P = {
    "noreply","no-reply","donotreply","webmaster","postmaster",
    "mailer","bounce","unsubscribe","abuse","spam","auto-reply",
    "notifications","alert","system","robot","daemon","do-not-reply",
}

def _is_hash(local_part: str) -> bool:
    """
    Detect if local part is a hash/token/encoded string — reject it.
    Catches Cloudflare-encoded emails that get mis-decoded,
    tracking IDs, session tokens, etc.
    """
    s = local_part.lower()
    length = len(s)

    # Pure hex string of 12+ chars = definitely a hash
    if re.match(r'^[0-9a-f]{12,}$', s): return True

    # Pure alphanumeric >20 chars with no vowels = hash
    if length > 12:
        vowels = sum(1 for c in s if c in "aeiou")
        ratio  = vowels / length
        if ratio == 0: return True           # Zero vowels
        if ratio < 0.08 and length > 16: return True  # Too few vowels

    # Very long with lots of digit/letter transitions = random token
    if length > 24:
        digits = sum(c.isdigit() for c in s)
        if digits > length * 0.35: return True  # >35% digits = suspicious

    # Starts with numbers = likely ID not name
    if re.match(r'^[0-9]{3,}', s) and length > 8: return True

    return False


def decode_cloudflare_email(encoded: str) -> str:
    """
    Decode Cloudflare email protection (XOR cipher).
    Cloudflare replaces emails with hex: data-cfemail="HEX" or href="#HEX"
    Decode: key = first byte; each subsequent byte XOR key → char
    """
    try:
        encoded = encoded.strip().lstrip("#").lower()
        # Must be valid hex, even length, at least 8 chars
        if not re.match(r'^[0-9a-f]+$', encoded): return ""
        if len(encoded) < 8 or len(encoded) % 2 != 0: return ""

        key = int(encoded[:2], 16)
        result = ""
        for i in range(2, len(encoded), 2):
            result += chr(int(encoded[i:i+2], 16) ^ key)

        # Validate: must look like an email
        if "@" in result and "." in result.split("@")[-1]:
            cleaned = result.strip().lower()
            # Final sanity: no weird characters
            if re.match(r'^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,10}$', cleaned):
                return cleaned
        return ""
    except:
        return ""
_UA_POOL       = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 Version/17.3 Safari/605.1.15",
]

def _clean_emails(emails):
    out = []
    for e in emails:
        e = e.strip().rstrip(".,;:")
        if not e or "@" not in e: continue
        e = e.lower()

        # Length sanity
        if len(e) < 6 or len(e) > 100: continue

        parts = e.split("@")
        if len(parts) != 2: continue
        local, dom = parts[0], parts[1]

        # Local part checks
        if len(local) < 1 or len(local) > 64: continue
        if _is_hash(local): continue  # reject hashes/tokens

        # Domain checks
        if not dom or "." not in dom: continue
        if dom in _BAD_EMAIL_D: continue
        tld = dom.rsplit(".", 1)[-1]
        if len(tld) < 2 or len(tld) > 10: continue
        if not tld.isalpha(): continue

        # Prefix blacklist
        if any(local == p or local.startswith(p + ".") or local.startswith(p + "-")
               for p in _BAD_EMAIL_P): continue

        # Reject file extensions in email
        if any(x in e for x in [".png",".jpg",".gif",".js",".css",
                                  ".svg",".ico",".woff",".ttf"]): continue

        # Reject paths/URLs mistakenly captured
        if "/" in local: continue

        # Reject clearly invalid local parts (starts with dot or has ..)
        if local.startswith(".") or ".." in local: continue

        out.append(e)
    return list(dict.fromkeys(out))

# Pages to check for emails (in priority order) — expanded list
_EMAIL_PAGES = [
    "",                  # homepage
    "/contact",          "/contact-us",       "/contact_us",
    "/contactus",        "/contact.html",     "/contact.php",
    "/about",            "/about-us",         "/about_us",
    "/about.html",       "/reach-us",         "/get-in-touch",
    "/enquiry",          "/enquire",          "/inquiry",
    "/info",             "/connect",          "/support",
    "/team",             "/our-team",         "/staff",
    "/people",           "/help",             "/hello",
    "/pages/contact",    "/pages/about",      # Shopify
    "/en/contact",       "/en/about",         # multilingual
    "/company",          "/who-we-are",
]

# Obfuscation patterns — expanded
_OBFUSC_PATTERNS = [
    # info [at] domain [dot] com
    (r'([\w.+\-]+)\s*[\[\(]at[\]\)]\s*([\w.\-]+)\s*[\[\(]dot[\]\)]\s*([\w]+)',
     lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}"),
    # info AT domain DOT com
    (r'([\w.+\-]+)\s+AT\s+([\w.\-]+)\s+DOT\s+([\w]+)',
     lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}"),
    # info(at)domain(dot)com
    (r'([\w.+\-]+)\(at\)([\w.\-]+)\(dot\)([\w]+)',
     lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}"),
    # info@domain&#46;com (HTML entity for dot)
    (r'([\w.+\-]+@[\w.\-]+)&#46;([\w]+)',
     lambda m: f"{m.group(1)}.{m.group(2)}"),
    # info _at_ domain _dot_ com
    (r'([\w.+\-]+)\s*_at_\s*([\w.\-]+)\s*_dot_\s*([\w]+)',
     lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}"),
    # info {@} domain {.} com (curly brace style)
    (r'([\w.+\-]+)\s*\{@\}\s*([\w.\-]+)\s*\{[.,]\}\s*([\w]+)',
     lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}"),
    # info <at> domain <dot> com
    (r'([\w.+\-]+)\s*<at>\s*([\w.\-]+)\s*<dot>\s*([\w]+)',
     lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}"),
    # Unicode @ symbol &#64; info&#64;domain.com
    (r'([\w.+\-]+)&#64;([\w.\-]+\.[\w]{2,7})',
     lambda m: f"{m.group(1)}@{m.group(2)}"),
]

def _extract_emails_from_html(html: str, base_url: str = "") -> list:
    """
    7-strategy email extraction from raw HTML.
    Strategy 0: Cloudflare email protection decode (PRIORITY)
    Strategy 1: Direct regex scan
    Strategy 2: mailto: links
    Strategy 3: Obfuscation patterns
    Strategy 4: data-email / data-cfemail attributes
    Strategy 5: HTML entity decode
    Strategy 6: JSON/JS embedded emails
    Strategy 7: HTML comments
    """
    found = []

    # ── Strategy 0: Cloudflare Email Protection ──────────────────
    # Cloudflare replaces emails with XOR-encoded hex strings
    # Format 1: href="/cdn-cgi/l/email-protection#HEX"
    cf_href_re = re.compile(r'href=["\']?/cdn-cgi/l/email-protection#([0-9a-fA-F]+)["\']?', re.I)
    for m in cf_href_re.finditer(html):
        decoded = decode_cloudflare_email(m.group(1))
        if decoded:
            found.insert(0, decoded)  # highest priority

    # Format 2: data-cfemail="HEX"
    for m in re.finditer(r'data-cfemail=["\']([0-9a-fA-F]+)["\']', html, re.I):
        decoded = decode_cloudflare_email(m.group(1))
        if decoded:
            found.insert(0, decoded)

    # ── Strategy 1: Direct regex ─────────────────────────────────
    found += _EMAIL_FIND_RE.findall(html)

    # ── Strategy 2: Mailto links ─────────────────────────────────
    for m in re.finditer(r"mailto:([^\s>?#&]+)", html, re.I):
        email = m.group(1).split("?")[0].strip().rstrip(".,;)")
        if email: found.append(email)

    # ── Strategy 3: Obfuscation patterns ─────────────────────────
    for pattern, replacer in _OBFUSC_PATTERNS:
        for m in re.finditer(pattern, html, re.I):
            try: found.append(replacer(m))
            except: pass

    # ── Strategy 4: data-email attributes ────────────────────────
    data_re = re.compile(
        r'data-[a-z]*mail[^>]{0,40}=\s*["\']([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,10})["\']',
        re.I)
    for m in data_re.finditer(html):
        found.append(m.group(1))

    # ── Strategy 5: HTML entity decode ───────────────────────────
    if "&#" in html:
        decoded = (html.replace("&#64;","@").replace("&#46;",".")
                   .replace("&#x40;","@").replace("&#x2E;",".")
                   .replace("&#X40;","@"))
        decoded = re.sub(r"&#0*64;","@",decoded)
        decoded = re.sub(r"&#0*46;",".",decoded)
        if decoded != html:
            found += _EMAIL_FIND_RE.findall(decoded)

    # ── Strategy 6: JSON/JS embedded emails ──────────────────────
    json_re = re.compile(
        r'"(?:email|mail|contact|email_address)"\s*:\s*"([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,10})"',
        re.I)
    for m in json_re.finditer(html):
        found.append(m.group(1))

    # ── Strategy 7: HTML comments ─────────────────────────────────
    for comment in re.finditer(r'<!--.*?-->', html, re.DOTALL):
        found += _EMAIL_FIND_RE.findall(comment.group(0))

    return found

def get_email_from_site(url: str, timeout: int = 8) -> str:
    """
    Comprehensive email finder:
    - Checks homepage + multiple sub-pages (up to 10)
    - 6 extraction strategies per page
    - Handles obfuscated emails
    - HTTP fallback when HTTPS fails
    - Tries both www and non-www variants
    - Returns best email (prefers non-generic)
    """
    if not url or str(url).strip() in ("","nan","-"): return ""
    if not url.startswith("http"): url = "https://" + url
    url = url.rstrip("/")

    base_domain = urlparse(url).netloc
    hdrs = {
        "User-Agent":      random.choice(_UA_POOL),
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }

    all_found = []
    visited   = set()
    homepage_html = ""   # cache homepage HTML to avoid double fetch

    def _try_get(page_url):
        """Try HTTPS, then HTTP fallback if SSL fails."""
        for try_url in [page_url]:
            try:
                r = rq.get(try_url, headers=hdrs, timeout=timeout,
                           verify=False, allow_redirects=True)
                if r.status_code == 200:
                    ct = r.headers.get("content-type","")
                    if "text" in ct or "html" in ct:
                        return r.text, r.url  # return final URL after redirect
                # Non-200 but not SSL error — try HTTP variant
                if try_url.startswith("https://"):
                    http_url = try_url.replace("https://","http://",1)
                    r2 = rq.get(http_url, headers=hdrs, timeout=timeout,
                                verify=False, allow_redirects=True)
                    if r2.status_code == 200:
                        ct = r2.headers.get("content-type","")
                        if "text" in ct or "html" in ct:
                            return r2.text, r2.url
            except rq.exceptions.SSLError:
                # SSL failed — retry with HTTP
                try:
                    http_url = try_url.replace("https://","http://",1)
                    r = rq.get(http_url, headers=hdrs, timeout=timeout,
                               verify=False, allow_redirects=True)
                    if r.status_code == 200:
                        return r.text, r.url
                except: pass
            except: pass
        return None, None

    def fetch_and_extract(page_url):
        if page_url in visited: return []
        visited.add(page_url)
        html, final_url = _try_get(page_url)
        if not html: return []
        # Also mark the final (redirected) URL as visited
        if final_url and final_url != page_url:
            visited.add(final_url.rstrip("/"))
        return _extract_emails_from_html(html, page_url)

    # ── Step 1: Homepage (cached) ──────────────────────────────────
    homepage_html, final_home = _try_get(url)
    if homepage_html:
        visited.add(url)
        if final_home:
            visited.add(final_home.rstrip("/"))
        home_emails = _extract_emails_from_html(homepage_html, url)
        all_found.extend(home_emails)

    # Check homepage results but don't exit early — keep collecting
    cleaned = _clean_emails(all_found)
    # Only exit early if we found a perfect specific email (personal/contact)
    if cleaned:
        best_so_far = max(cleaned, key=_email_quality)
        if _email_quality(best_so_far) >= 90:
            return best_so_far

    # ── Step 2: Parse links + inject known paths from cached HTML ──
    internal_links = []
    if homepage_html:
        try:
            soup = BeautifulSoup(homepage_html, "html.parser")

            for a in soup.find_all("a", href=True):
                href = a.get("href","")
                text = a.get_text("",strip=True).lower()
                href_l = href.lower()

                is_priority = any(k in href_l or k in text for k in [
                    "contact","about","reach","info","email","enquir",
                    "team","staff","people","connect","support","help",
                    "hello","company","who-we-are",
                ])

                if href_l.startswith("mailto:"):
                    email = href[7:].split("?")[0].strip()
                    all_found.append(email)
                    continue

                full = urljoin(url, href)
                parsed_full = urlparse(full)
                # Accept same domain and same base domain (handles www. differences)
                if (parsed_full.netloc == base_domain or
                        parsed_full.netloc == base_domain.replace("www.","") or
                        parsed_full.netloc == "www." + base_domain.replace("www.","")):
                    clean_full = full.rstrip("/")
                    if clean_full not in visited:
                        if is_priority:
                            internal_links.insert(0, clean_full)
                        else:
                            internal_links.append(clean_full)
        except: pass

    # ── Step 3: Inject known contact paths at front of queue ───────
    for path in _EMAIL_PAGES[1:]:   # skip "" (homepage already done)
        test_url = url + path
        if test_url not in visited and test_url not in internal_links:
            internal_links.insert(0, test_url)

    # ── Step 4: Visit up to 10 pages ──────────────────────────────
    checked = 0
    seen_urls = set()
    for link in internal_links:
        if checked >= 10: break
        if link in seen_urls: continue
        seen_urls.add(link)

        emails = fetch_and_extract(link)
        all_found.extend(emails)
        checked += 1

        cleaned2 = _clean_emails(all_found)
        if cleaned2:
            best2 = max(cleaned2, key=_email_quality)
            if _email_quality(best2) >= 85:
                return best2

        time.sleep(0.2)

    # ── Step 5: Try www / non-www variant if nothing found ────────
    if not _clean_emails(all_found):
        parsed = urlparse(url)
        if parsed.netloc.startswith("www."):
            alt_url = url.replace("www.", "", 1)
        else:
            alt_url = url.replace("://", "://www.", 1)
        if alt_url != url:
            alt_emails = fetch_and_extract(alt_url)
            all_found.extend(alt_emails)

    # ── Step 6: HTTP fallback if HTTPS gave nothing ────────────────
    if not _clean_emails(all_found) and url.startswith("https://"):
        http_url = url.replace("https://", "http://", 1)
        all_found.extend(fetch_and_extract(http_url))

    cleaned_final = _clean_emails(all_found)
    if cleaned_final:
        # Return email with highest quality score
        return max(cleaned_final, key=_email_quality)
    return ""


def _email_quality(email: str) -> int:
    """
    Score email quality 0-100.
    Higher = more likely real business contact email.
    """
    if not email or "@" not in email: return 0
    local  = email.split("@")[0].lower()
    domain = email.split("@")[1].lower()

    # Instant reject if hash
    if _is_hash(local): return 0

    score = 40  # base

    # Best locals — business contact emails
    top_locals = ["contact","info","hello","enquir","enquiry","enquiries",
                  "office","admin","sales","support","booking","reserv",
                  "reception","mail","team","reach","help","service"]
    ok_locals  = ["accounts","billing","marketing","media","pr","hr",
                  "operations","manager","director","owner","partner"]
    bad_locals = ["noreply","no-reply","donotreply","unsubscribe","bounce",
                  "mailer","postmaster","webmaster","abuse","daemon","robot",
                  "alert","notification","system","auto"]

    if any(local.startswith(b) for b in bad_locals):
        return 0  # Hard reject

    if any(local == g or local.startswith(g) for g in top_locals):
        score += 35
    elif any(local.startswith(o) for o in ok_locals):
        score += 20

    # Personal name pattern: john.doe@ or jdoe@ or john@
    if re.match(r'^[a-z]{2,}\.[a-z]{2,}$', local):  # john.doe
        score += 25
    elif re.match(r'^[a-z]{2,12}$', local):           # john
        score += 15
    elif re.match(r'^[a-z][a-z0-9]{0,2}[a-z]{2,}$', local):  # jdoe
        score += 10

    # Penalty for long cryptic local part
    if len(local) > 30: score -= 30
    if len(local) > 20: score -= 15
    if len(local) > 15: score -= 5

    # Domain: business domain > free email
    free_domains = {"gmail.com","yahoo.com","hotmail.com","outlook.com",
                    "live.com","icloud.com","aol.com","protonmail.com"}
    if domain in free_domains:
        score += 5   # Still valid, just not business domain
    elif "." in domain:
        score += 15  # Business domain = better

    return max(0, min(100, score))

# ═════════════════════════════════════════════════════════════════════
#  SCRAPER METHOD 1: Google Places API (Official)
# ═════════════════════════════════════════════════════════════════════
def scrape_places_api(api_key: str, keyword: str, location: str,
                      max_results: int, log_cb, stop_flag) -> list:
    results = []
    base    = "https://maps.googleapis.com/maps/api/place"

    # Step 1: Geocode location via Text Search (FIX: was using wrong endpoint /places/json)
    log_cb(f"📍 Geocoding: {location}", "info")
    geo = rq.get(f"{base}/textsearch/json",
                 params={"query": location, "key": api_key}, timeout=8)
    geo_data = geo.json()
    if geo_data.get("status") != "OK":
        log_cb(f"❌ Places API: Could not geocode '{location}' — {geo_data.get('status','')}", "error")
        return []

    loc = geo_data["results"][0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]
    log_cb(f"✅ Location: {lat}, {lng}", "success")

    # Step 2: Nearby Search (paginate up to 3 pages = 60 results)
    params = {
        "location":  f"{lat},{lng}",
        "radius":    20000,
        "keyword":   keyword,
        "key":       api_key,
    }
    next_token = None

    while len(results) < max_results and not stop_flag():
        if next_token:
            params2 = {"pagetoken": next_token, "key": api_key}
            time.sleep(2)  # Google requires delay before next_page_token works
            r = rq.get(f"{base}/nearbysearch/json", params=params2, timeout=10)
        else:
            r = rq.get(f"{base}/nearbysearch/json", params=params, timeout=10)

        data = r.json()
        status = data.get("status","")

        if status not in ("OK","ZERO_RESULTS"):
            log_cb(f"❌ API Error: {status} — {data.get('error_message','')}", "error")
            break

        for place in data.get("results", []):
            if stop_flag() or len(results) >= max_results: break

            # Step 3: Place Details for phone, website, hours
            pid = place.get("place_id","")
            detail_r = rq.get(f"{base}/details/json", params={
                "place_id": pid,
                "fields": "name,formatted_phone_number,formatted_address,"
                          "website,rating,user_ratings_total,"
                          "opening_hours,business_status,types",
                "key": api_key,
            }, timeout=8)
            det = detail_r.json().get("result", {})

            biz = {
                "name":     det.get("name", place.get("name","")),
                "category": ", ".join(place.get("types",[]))[:60],
                "rating":   str(det.get("rating", place.get("rating",""))),
                "reviews":  str(det.get("user_ratings_total",
                                         place.get("user_ratings_total",""))),
                "phone":    det.get("formatted_phone_number",""),
                "website":  det.get("website",""),
                "address":  det.get("formatted_address",
                                     place.get("vicinity","")),
                "hours":    "Open" if det.get("opening_hours",{}).get("open_now") else "",
                "source":   "Google Places API",
            }
            results.append(biz)
            log_cb(f"[{len(results)}] ✅ {biz['name'][:45]}", "success")

        next_token = data.get("next_page_token")
        if not next_token: break

    return results

# ═════════════════════════════════════════════════════════════════════
#  SCRAPER METHOD 2: SerpAPI
# ═════════════════════════════════════════════════════════════════════
def scrape_serpapi(api_key: str, keyword: str, location: str,
                   max_results: int, log_cb, stop_flag) -> list:
    results = []
    start   = 0

    while len(results) < max_results and not stop_flag():
        log_cb(f"🔍 SerpAPI: {keyword} in {location} (start={start})", "info")
        try:
            r = rq.get("https://serpapi.com/search", params={
                "engine":   "google_maps",
                "q":        f"{keyword} {location}",
                "type":     "search",
                "start":    start,
                "api_key":  api_key,
            }, timeout=15)
            data = r.json()

            if "error" in data:
                log_cb(f"❌ SerpAPI: {data['error']}", "error")
                break

            local_results = data.get("local_results", [])
            if not local_results:
                log_cb("📋 No more results from SerpAPI", "info")
                break

            for place in local_results:
                if stop_flag() or len(results) >= max_results: break
                biz = {
                    "name":     place.get("title",""),
                    "category": place.get("type",""),
                    "rating":   str(place.get("rating","")),
                    "reviews":  str(place.get("reviews","")),
                    "phone":    place.get("phone",""),
                    "website":  place.get("website",""),
                    "address":  place.get("address",""),
                    "hours":    place.get("hours",""),
                    "source":   "SerpAPI",
                }
                results.append(biz)
                log_cb(f"[{len(results)}] ✅ {biz['name'][:45]}", "success")

            start += 20

        except Exception as e:
            log_cb(f"❌ SerpAPI error: {e}", "error")
            break

    return results

# ═════════════════════════════════════════════════════════════════════
#  SCRAPER METHOD 3: Raw HTTP (N8N-style — no browser needed)
#  Parses embedded JSON from Google Maps page source
# ═════════════════════════════════════════════════════════════════════
def scrape_raw_http(keyword: str, location: str, max_results: int,
                    log_cb, stop_flag) -> list:
    """
    Fetches Google Maps search page directly and parses
    the embedded JSON data (same approach used in n8n automations).
    No Selenium needed — pure HTTP requests.
    """
    results  = []
    session  = rq.Session()
    ua       = random.choice(_UA_POOL)
    headers  = {
        "User-Agent":      ua,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer":         "https://www.google.com/",
    }

    query    = f"{keyword} {location}"
    enc_q    = quote_plus(query)
    url      = f"https://www.google.com/maps/search/{enc_q}/"

    log_cb(f"🌐 Fetching Maps page: {query}", "info")

    try:
        r = session.get(url, headers=headers, timeout=15)
        html = r.text

        log_cb(f"📄 Page fetched ({len(html)//1024}KB). Parsing JSON...", "info")

        # Google Maps embeds business data in JS as:
        # window.APP_INITIALIZATION_STATE = [...]
        # OR as a large JSON blob

        # Pattern 1: Extract all /*""*/ blocks which contain listing data
        matches = re.findall(r'\"\"]\s*,\s*\[null,null,null,\[null,null,\[(.*?)\]\]\]', html)

        # Pattern 2: Look for the specific places data structure
        # Google stores it as: )]}'  followed by JSON
        clean_pattern = re.search(r'\)]\}\'(.+)', html, re.DOTALL)

        # Pattern 3: Extract name+rating+address from visible text patterns
        # This is the most reliable cross-version approach
        # Find entries matching: "Business Name", then coordinates, rating etc.

        businesses_raw = []

        # Method A: Parse APP_INITIALIZATION_STATE
        init_match = re.search(r'APP_INITIALIZATION_STATE\s*=\s*(\[.+?\]);\s*window', html, re.DOTALL)
        if init_match:
            try:
                raw_json = init_match.group(1)
                # This JSON is very deeply nested; extract business names + info
                names_in_json = re.findall(r'"([^"]{3,60})"', raw_json)
                log_cb(f"  Found JSON blob with {len(names_in_json)} string tokens", "info")
            except: pass

        # Method B: Most reliable — extract from the structured URL patterns
        # Google Maps includes place data in the URL format /maps/place/NAME/
        place_urls = re.findall(r'/maps/place/([^/]+)/(?:@([\d\.\-]+),([\d\.\-]+))?', html)
        log_cb(f"  Found {len(place_urls)} place patterns in page", "info")

        # Method C: Parse the structured data blocks Google uses
        # Look for the pattern that contains business listings
        listing_blocks = re.findall(
            r'\[null,\s*"([^"]{3,80})".*?'   # name
            r'(?:"([^"]{3,80})")?.*?'          # category
            r'(\d\.\d).*?'                     # rating
            r'\((\d+)\)',                       # review count
            html,
            re.DOTALL   # FIX: without this, . doesn't match newlines so blocks are missed
        )

        seen = set()
        for block in listing_blocks:
            if stop_flag() or len(results) >= max_results: break
            name, cat, rating, reviews = block
            if name in seen or len(name) < 3: continue
            # Filter obvious non-business strings
            if any(x in name.lower() for x in ["https","javascript","function",
                                                "window","null","undefined"]): continue
            seen.add(name)
            biz = {
                "name":     name,
                "category": cat or "",
                "rating":   rating,
                "reviews":  reviews,
                "phone":    "",
                "website":  "",
                "address":  "",
                "hours":    "",
                "source":   "Raw HTTP",
            }
            results.append(biz)
            log_cb(f"[{len(results)}] ✅ {name[:45]} ⭐{rating} ({reviews} reviews)", "success")

        if len(results) == 0:
            log_cb("⚠️ Raw HTTP: Limited results. Try Selenium mode for full extraction.", "warn")

    except Exception as e:
        log_cb(f"❌ Raw HTTP error: {e}", "error")

    return results

# ═════════════════════════════════════════════════════════════════════
#  SCRAPER METHOD 4: Selenium (Robust JS-injection approach)
# ═════════════════════════════════════════════════════════════════════
def scrape_selenium(keyword: str, location: str, max_results: int,
                    headless: bool, log_cb, stop_flag) -> list:
    if not _selenium_ok:
        log_cb("❌ Selenium not installed", "error")
        return []

    results = []
    seen    = set()

    opts = webdriver.ChromeOptions()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={random.choice(_UA_POOL)}")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--lang=en-US,en")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-popup-blocking")
    opts.add_experimental_option("excludeSwitches", ["enable-automation","enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)
    if headless:
        opts.add_argument("--headless=new")

    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install(), log_path=os.devnull),
            options=opts
        )
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
            Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
            Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
            Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
            window.chrome={runtime:{}};
        """})
    except Exception as e:
        log_cb(f"❌ Chrome failed: {e}", "error")
        return []

    query = f"{keyword} {location}"
    url   = f"https://www.google.com/maps/search/{quote_plus(query)}"

    try:
        log_cb(f"🌍 Opening Google Maps: {query}", "info")
        driver.get(url)
        time.sleep(4)

        # Dismiss consent dialogs
        for sel in ["button[jsname='higCR']","#L2AGLb","button[aria-label*='Accept all']",
                    "button[aria-label*='Reject all']","form[action*='consent'] button:last-child"]:
            try:
                driver.find_element(By.CSS_SELECTOR, sel).click()
                time.sleep(1.5); break
            except: continue

        # Wait for sidebar/feed
        log_cb("⏳ Loading results panel...", "info")
        time.sleep(3)

        # ── SCROLL TO LOAD ALL LISTINGS ──────────────────────────────
        # Google Maps renders results in a scrollable left panel
        # We use JS to scroll that panel directly
        log_cb("📜 Scrolling to load all results...", "info")

        scroll_js = """
        var feed = document.querySelector('div[role="feed"]') ||
                   document.querySelector('.m6QErb') ||
                   document.querySelector('[aria-label*="Results"]');
        if(feed){ feed.scrollTop += 1500; return feed.scrollTop; }
        return -1;
        """

        prev_count = 0
        stale      = 0
        iteration  = 0

        while stale < 8 and not stop_flag():
            # Count current listings using JS
            count_js = """
            var items = document.querySelectorAll('a.hfpxzc') ||
                        document.querySelectorAll('div[role="feed"] a[href*="maps/place"]');
            return (document.querySelectorAll('a.hfpxzc').length ||
                    document.querySelectorAll('div[role=\"feed\"] a[href*=\"maps/place\"]').length);
            """
            try:
                current = driver.execute_script(
                    "return (document.querySelectorAll('a.hfpxzc').length || "
                    "document.querySelectorAll('div.Nv2PK').length || "
                    "document.querySelectorAll('[data-result-index]').length);"
                )
                current = int(current or 0)
            except:
                current = 0

            log_cb(f"  Scroll {iteration+1}: {current} listings loaded", "info")

            if current >= max_results:
                log_cb(f"  ✅ Target reached ({max_results})", "info")
                break

            if current == prev_count:
                stale += 1
                # Try harder scroll
                try:
                    driver.execute_script(scroll_js)
                    driver.execute_script("window.scrollBy(0,500)")
                except: pass
            else:
                stale = 0

            prev_count = current
            try:
                driver.execute_script(scroll_js)
            except: pass

            # Check end of list
            try:
                body_text = driver.find_element(By.TAG_NAME,"body").text
                if "reached the end" in body_text.lower() or "no more results" in body_text.lower():
                    log_cb("📋 End of results reached.", "info")
                    break
            except: pass

            time.sleep(random.uniform(1.2, 2.0))
            iteration += 1

        # ── GET ALL LISTING ELEMENTS ─────────────────────────────────
        # Try multiple selectors to find business cards
        cards = []
        for sel in [
            "a.hfpxzc",                              # Most common
            "div.Nv2PK a",                            # Container variant
            "div[role='feed'] > div > div a",         # Generic feed items
            "div[jstcache] a[href*='/maps/place/']",  # Place links
        ]:
            try:
                found = driver.find_elements(By.CSS_SELECTOR, sel)
                if len(found) > len(cards):
                    cards = found
            except: pass

        total = min(len(cards), max_results)
        log_cb(f"✅ Found {len(cards)} listings. Extracting {total}...", "success")

        if total == 0:
            # Last resort: try clicking visible items by XPATH text search
            log_cb("⚠️ No cards found with CSS. Trying XPath...", "warn")
            try:
                cards = driver.find_elements(By.XPATH,
                    "//a[contains(@href,'/maps/place/')]")
                total = min(len(cards), max_results)
                log_cb(f"  XPath found: {len(cards)}", "info")
            except: pass

        # ── EXTRACT EACH BUSINESS ────────────────────────────────────
        for idx in range(total):
            if stop_flag(): break
            try:
                # Re-fetch cards each time (DOM may change)
                fresh_cards = []
                for sel in ["a.hfpxzc","div.Nv2PK a",
                            "div[role='feed'] > div > div a"]:
                    try:
                        fc = driver.find_elements(By.CSS_SELECTOR, sel)
                        if len(fc) > len(fresh_cards):
                            fresh_cards = fc
                    except: pass

                if idx >= len(fresh_cards):
                    break

                card = fresh_cards[idx]

                # Scroll into view
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center',behavior:'smooth'})", card)
                time.sleep(random.uniform(0.5, 1.0))

                # Click — try normal then JS click
                try:
                    card.click()
                except:
                    try:
                        driver.execute_script("arguments[0].click()", card)
                    except: continue

                # Wait for detail panel to load
                time.sleep(random.uniform(2.0, 3.5))
                # Verify detail panel opened (h1 present)
                try:
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
                    )
                except:
                    pass  # Continue anyway

                biz = _extract_business_details(driver)

                if not biz.get("name","").strip():
                    continue

                nk = biz["name"].lower().strip()
                if nk in seen:
                    continue
                seen.add(nk)

                biz["source"] = "Selenium"
                results.append(biz)

                log_cb(
                    f"[{len(results)}/{total}] ✅ {biz['name'][:40]}"
                    f" | ⭐{biz.get('rating','')} | 📞{biz.get('phone','—')}",
                    "success"
                )

                # Anti-block delay
                delay = random.uniform(1.5, 3.0)
                if random.random() < 0.06:
                    delay += random.uniform(3, 7)
                    log_cb("  (human pause...)", "info")
                time.sleep(delay)

            except Exception as e:
                log_cb(f"  [{idx+1}] ⚠️ Skipped: {str(e)[:60]}", "warn")
                continue

    except Exception as e:
        log_cb(f"❌ Fatal Selenium error: {e}", "error")
    finally:
        try: driver.quit()
        except: pass

    return results

def _extract_business_details(driver) -> dict:
    """Extract all fields from the currently open business panel."""
    biz = {k:"" for k in ["name","category","rating","reviews","phone",
                            "website","address","hours"]}

    def safe(sel, attr=None):
        for s in (sel if isinstance(sel, list) else [sel]):
            try:
                el = driver.find_element(By.CSS_SELECTOR, s)
                return (el.get_attribute(attr) if attr else el.text.strip()) or ""
            except: continue
        return ""

    # Name
    biz["name"] = safe(["h1.DUwDvf","h1.fontHeadlineLarge","h1[class*='fontHeadline']","h1"])

    # Rating
    for sel in ["div.F7nice span[aria-hidden='true']","span.MW4etd","div.fontBodyMedium span[aria-hidden]"]:
        v = safe(sel)
        if v and re.match(r'[\d\.]+', v):
            biz["rating"] = v; break

    # Reviews
    try:
        for sel in ["div.F7nice span[aria-label]","button[aria-label*='review']","span.UY7F9"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                lbl = el.get_attribute("aria-label") or el.text
                m = re.search(r'([\d,]+)\s*review', lbl, re.I)
                if m:
                    biz["reviews"] = m.group(1).replace(",","")
                    break
            if biz["reviews"]: break
    except: pass

    # Category
    biz["category"] = safe(["button.DkEaL","div.fontBodyMedium.dmRWX","span.YhemCb"])

    # Phone
    for sel in ["button[data-item-id*='phone'] .Io6YTe",
                "a[href^='tel:']", "[data-tooltip='Copy phone number']",
                "span.UsdlK"]:
        v = safe(sel) or safe(sel, "href")
        if v:
            biz["phone"] = v.replace("tel:","").strip()
            break

    # Website
    for sel in ["a[data-item-id='authority']","a[aria-label*='website' i]",
                "a.lcr4fd","a[data-item-id*='authority']"]:
        v = safe(sel, "href")
        if v and v.startswith("http"):
            biz["website"] = v; break

    # Address
    for sel in ["button[data-item-id*='address'] .Io6YTe",
                "div[data-item-id*='latlng'] .fontBodyMedium",
                ".rogA2c .Io6YTe"]:
        v = safe(sel)
        if v and len(v) > 5:
            biz["address"] = v; break

    # Hours
    try:
        for sel in ["div[aria-label*='hour' i]","table.y0skZc",
                    "div[data-hide-tooltip-on-mouse-move]"]:
            v = safe(sel)
            if v:
                biz["hours"] = v[:80]
                break
    except: pass

    return biz

# ═════════════════════════════════════════════════════════════════════
#  BULK EMAIL FINDER ENGINE
# ═════════════════════════════════════════════════════════════════════
def bulk_find_email(domain: str) -> dict:
    """Find email for a single domain. Returns result dict."""
    result = {
        "domain":      domain.strip(),
        "email":       "",
        "email_valid": "",
        "email_score": "",
        "website":     "",
        "status":      "Processing",
        "error":       "",
    }
    try:
        url = domain.strip()
        if not url:
            result["status"] = "Skipped"
            return result

        # Normalize URL
        if not url.startswith("http"):
            url = "https://" + url
        result["website"] = url

        email = get_email_from_site(url, timeout=10)
        result["email"] = email

        if email:
            vr = verify_email(email)
            result["email_valid"] = "✅" if vr["valid"] else "❌"
            result["email_score"] = str(vr["score"])
            result["status"] = "Found"
        else:
            # Try www variant
            parsed = urlparse(url)
            alt = url.replace(parsed.netloc, "www." + parsed.netloc.replace("www.",""))
            if alt != url:
                email2 = get_email_from_site(alt, timeout=8)
                if email2:
                    vr = verify_email(email2)
                    result["email"]       = email2
                    result["email_valid"] = "✅" if vr["valid"] else "❌"
                    result["email_score"] = str(vr["score"])
                    result["status"]      = "Found"
                    return result
            result["status"] = "Not Found"
    except Exception as e:
        result["status"] = "Error"
        result["error"]  = str(e)[:80]
    return result


# ═════════════════════════════════════════════════════════════════════
#  HEATMAP ENGINE
# ═════════════════════════════════════════════════════════════════════
def geocode_location(location: str) -> tuple:
    """Get lat/lng for a location using OSM Nominatim (free)."""
    try:
        r = rq.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": "LeadHunterPro/3.0"},
            timeout=8
        )
        if r.status_code == 200 and r.json():
            item = r.json()[0]
            return float(item["lat"]), float(item["lon"])
    except: pass
    return None, None


def build_grid(center_lat: float, center_lng: float,
               radius_km: float, grid_size: int) -> list:
    """Build a grid of lat/lng points around center."""
    # 1 degree lat ≈ 111 km, 1 degree lng ≈ 111*cos(lat) km
    # FIX: guard against grid_size=1 causing division by zero
    if grid_size <= 1:
        return [{"lat": round(center_lat, 6), "lng": round(center_lng, 6),
                 "row": 0, "col": 0}]
    lat_step = (radius_km * 2 / (grid_size - 1)) / 111.0
    lng_step = (radius_km * 2 / (grid_size - 1)) / (111.0 * math.cos(math.radians(center_lat)))

    points = []
    for row in range(grid_size):
        for col in range(grid_size):
            lat = center_lat - radius_km/111.0 + row * lat_step
            lng = center_lng - radius_km/(111.0 * math.cos(math.radians(center_lat))) + col * lng_step
            points.append({
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "row": row,
                "col": col,
            })
    return points


def check_rank_selenium(keyword: str, business_name: str,
                         lat: float, lng: float,
                         radius_m: int, log_cb) -> dict:
    """Check rank of business_name for keyword at lat/lng using Selenium."""
    result = {"rank": -1, "found": False, "businesses": []}
    if not _selenium_ok:
        return result

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={random.choice(_UA_POOL)}")
    opts.add_argument("--log-level=3")
    opts.add_experimental_option("excludeSwitches",["enable-automation","enable-logging"])

    driver = None  # FIX: initialize to None so except block won't NameError
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install(), log_path=os.devnull),
            options=opts
        )
        # Use Google Maps URL with center coordinates
        query = quote_plus(keyword)
        url = f"https://www.google.com/maps/search/{query}/@{lat},{lng},14z"
        driver.get(url)
        time.sleep(3)

        # Dismiss consent
        for sel in ["button[jsname='higCR']","#L2AGLb","form[action*='consent'] button"]:
            try:
                driver.find_element(By.CSS_SELECTOR, sel).click()
                time.sleep(1); break
            except: continue

        time.sleep(2)

        # Get listing names
        for sel in ["a.hfpxzc","div.Nv2PK a"]:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if len(cards) > 1: break

        names = []
        for i, card in enumerate(cards[:20]):
            try:
                name = card.get_attribute("aria-label") or card.text.strip()
                if name:
                    names.append(name.strip())
            except: pass

        result["businesses"] = names[:20]

        # Find rank
        bname_lower = business_name.lower().strip()
        for i, n in enumerate(names):
            if bname_lower in n.lower() or n.lower() in bname_lower:
                result["rank"]  = i + 1
                result["found"] = True
                break

        if not result["found"]:
            result["rank"] = 21  # Not in top 20

        driver.quit()
    except Exception as e:
        log_cb(f"  Selenium rank check error: {e}", "warn")
        if driver:
            try: driver.quit()
            except: pass

    return result


def check_rank_places_api(api_key: str, keyword: str, business_name: str,
                           lat: float, lng: float, radius_m: int) -> dict:
    """Check rank using Google Places API."""
    result = {"rank": -1, "found": False, "businesses": []}
    try:
        r = rq.get(
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
            params={
                "location":  f"{lat},{lng}",
                "radius":    radius_m,
                "keyword":   keyword,
                "key":       api_key,
            }, timeout=10
        )
        if r.status_code == 200:
            items = r.json().get("results", [])
            names = [x.get("name","") for x in items]
            result["businesses"] = names

            bname_lower = business_name.lower().strip()
            for i, n in enumerate(names):
                if bname_lower in n.lower() or n.lower() in bname_lower:
                    result["rank"]  = i + 1
                    result["found"] = True
                    break
            if not result["found"]:
                result["rank"] = len(names) + 1 if names else 21
    except Exception as e:
        pass
    return result


def generate_heatmap_html(keyword: str, business_name: str,
                           center_lat: float, center_lng: float,
                           grid_results: list, grid_size: int,
                           location_name: str) -> str:
    """Generate interactive heatmap HTML using Folium + OSM."""
    try:
        import folium
    except ImportError:
        return ""

    # Color mapping based on rank
    def rank_color(rank):
        if rank <= 0:   return "#94A3B8"  # grey - no data
        if rank <= 3:   return "#22C55E"  # green - top 3
        if rank <= 7:   return "#86EFAC"  # light green - 4-7
        if rank <= 10:  return "#FDE047"  # yellow - 8-10
        if rank <= 15:  return "#FB923C"  # orange - 11-15
        if rank <= 20:  return "#EF4444"  # red - 16-20
        return "#7F1D1D"                   # dark red - 20+

    def rank_label(rank):
        if rank <= 0:  return "No data"
        if rank <= 3:  return f"#{rank} 🏆 Top 3"
        if rank <= 7:  return f"#{rank} ✅ Top 7"
        if rank <= 10: return f"#{rank} 👍 Top 10"
        if rank <= 20: return f"#{rank} ⚠️ Top 20"
        return "❌ Not ranked"

    # Create map centered on location
    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=13,
        tiles="OpenStreetMap",
        attr="© OpenStreetMap contributors"
    )

    # Add title
    title_html = f"""
    <div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);
                z-index:9999;background:#0F1623;color:#38BDF8;
                padding:10px 20px;border-radius:8px;
                font-family:Segoe UI;font-size:14px;font-weight:bold;
                border:1px solid #1E2D45;box-shadow:0 4px 12px rgba(0,0,0,0.5);">
        🗺️ LeadHunter Heatmap — <span style="color:#F59E0B">{business_name}</span>
        &nbsp;|&nbsp; Keyword: <span style="color:#34D399">{keyword}</span>
        &nbsp;|&nbsp; {location_name}
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Legend
    legend_html = """
    <div style="position:fixed;bottom:30px;right:10px;z-index:9999;
                background:#0F1623;color:white;padding:12px 16px;
                border-radius:8px;font-family:Segoe UI;font-size:11px;
                border:1px solid #1E2D45;">
        <b style="color:#38BDF8">RANK LEGEND</b><br>
        <span style="color:#22C55E">●</span> #1-3  Top 3 🏆<br>
        <span style="color:#86EFAC">●</span> #4-7  Good ✅<br>
        <span style="color:#FDE047">●</span> #8-10 Average<br>
        <span style="color:#FB923C">●</span> #11-15 Weak<br>
        <span style="color:#EF4444">●</span> #16-20 Poor ⚠️<br>
        <span style="color:#7F1D1D">●</span> 20+  Not ranked ❌
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # Add center marker (the business)
    folium.Marker(
        [center_lat, center_lng],
        popup=f"<b>{business_name}</b><br>Target Business",
        tooltip=f"📍 {business_name}",
        icon=folium.Icon(color="blue", icon="star", prefix="fa")
    ).add_to(m)

    # Add grid points
    for pt in grid_results:
        rank  = pt.get("rank", -1)
        color = rank_color(rank)
        label = rank_label(rank)
        biz_list = "<br>".join(
            [f"{i+1}. {b}" for i,b in enumerate(pt.get("businesses",[])[:5])])

        popup_html = f"""
        <div style="font-family:Segoe UI;font-size:12px;min-width:200px">
            <b style="color:{color}">{label}</b><br>
            <hr style="margin:4px 0">
            <b>Top results here:</b><br>
            {biz_list if biz_list else "No results"}
        </div>
        """
        # Circle marker
        folium.CircleMarker(
            location=[pt["lat"], pt["lng"]],
            radius=18,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            weight=2,
            tooltip=label,
            popup=folium.Popup(popup_html, max_width=250),
        ).add_to(m)

        # Rank number inside circle
        folium.Marker(
            [pt["lat"], pt["lng"]],
            icon=folium.DivIcon(
                html=f'<div style="font-size:11px;font-weight:bold;color:white;'
                     f'text-align:center;line-height:20px;'
                     f'text-shadow:0 1px 2px rgba(0,0,0,0.8);">'
                     f'{"#"+str(rank) if 0 < rank <= 20 else "20+"}</div>',
                icon_size=(36,20),
                icon_anchor=(18,10),
            )
        ).add_to(m)

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False,
                                       prefix="leadhunter_heatmap_")
    m.save(tmp.name)
    return tmp.name


# ═════════════════════════════════════════════════════════════════════
#  MASTER SCRAPER — orchestrates method selection + enrichment
# ═════════════════════════════════════════════════════════════════════
class MasterScraper:
    def __init__(self, log_cb, row_cb, status_cb, progress_cb):
        self.log_cb      = log_cb
        self.row_cb      = row_cb
        self.status_cb   = status_cb
        self.progress_cb = progress_cb
        self._stop       = False
        self._pause      = False

    def stop(self):   self._stop  = True
    def pause(self):  self._pause = True
    def resume(self): self._pause = False

    def _wait_if_paused(self):
        while self._pause and not self._stop:
            time.sleep(0.5)

    def run(self, method: str, keyword: str, location: str,
            max_results: int, options: dict,
            places_api_key: str = "", serp_api_key: str = "") -> list:

        self._stop  = False
        self._pause = False
        raw_results = []

        self.log_cb("━"*55, "info")
        self.log_cb(f"🚀 START: {keyword} | {location} | Method: {method}", "info")
        self.log_cb("━"*55, "info")

        # ── Scrape ───────────────────────────────────────────
        if method == "Google Places API":
            if not places_api_key:
                self.log_cb("❌ No Google Places API key set!", "error"); return []
            raw_results = scrape_places_api(
                places_api_key, keyword, location,
                max_results, self.log_cb, lambda: self._stop)

        elif method == "SerpAPI":
            if not serp_api_key:
                self.log_cb("❌ No SerpAPI key set!", "error"); return []
            raw_results = scrape_serpapi(
                serp_api_key, keyword, location,
                max_results, self.log_cb, lambda: self._stop)

        elif method == "Raw HTTP (Fast)":
            raw_results = scrape_raw_http(
                keyword, location, max_results,
                self.log_cb, lambda: self._stop)

        else:  # Selenium (default)
            raw_results = scrape_selenium(
                keyword, location, max_results,
                options.get("headless", False),
                self.log_cb, lambda: self._stop)

        self.log_cb(f"📊 Raw results: {len(raw_results)}", "info")

        # ── Enrichment ───────────────────────────────────────
        final = []
        total = len(raw_results)

        for idx, biz in enumerate(raw_results):
            if self._stop: break
            self._wait_if_paused()

            self.status_cb(f"⚙️ Enriching {idx+1}/{total}: {biz.get('name','')[:35]}...")
            self.progress_cb(idx+1, total)

            # Reviews/rating as int/float
            try:    reviews_int = int(str(biz.get("reviews","0")).replace(",","")) 
            except: reviews_int = 0
            try:    rating_f = float(biz.get("rating","0") or 0)
            except: rating_f = 0.0

            biz["reviews"] = str(reviews_int)

            # ── Filter fake/invalid business names ───────────────
            name = biz.get("name","").strip()
            if not name or len(name) < 3:
                continue
            # Reject if name is just a number/date/symbol
            if re.match(r'^[\d\.\-\/\s\(\)]+$', name):
                self.log_cb(f"  ⚠️ Skipping invalid name: {name}", "warn")
                continue
            # Reject very long strings (not business names)
            if len(name) > 120:
                continue
            # Reject obvious non-business strings
            bad_patterns = ["javascript","function()","undefined","null",
                            "http://","https://","www.","<div","<span",
                            "google","viewport","charset"]
            if any(p in name.lower() for p in bad_patterns):
                continue

            # Apply filters
            min_r = options.get("min_rating", 0.0)
            max_rv= options.get("max_reviews", 999999)
            new_only = options.get("new_only", False)

            if rating_f < min_r and rating_f != 0: continue
            if reviews_int > max_rv and max_rv > 0: continue
            if new_only and reviews_int > 15: continue

            # Is new business?
            biz["is_new"] = "✅ YES" if reviews_int <= 15 else "NO"

            # Find email from website
            biz.setdefault("email", "")
            website = biz.get("website","").strip()
            if options.get("find_emails", True) and website and website != "nan":
                self.log_cb(f"  📧 Finding email: {biz.get('name','')[:30]}...", "info")
                found_email = get_email_from_site(website)
                biz["email"] = found_email
                if found_email:
                    self.log_cb(f"  ✅ Email: {found_email}", "success")
                else:
                    self.log_cb(f"  — No email found on website", "info")

            # Email verification
            if options.get("verify_emails", True) and biz.get("email",""):
                vr = verify_email(biz["email"])
                biz["email_valid"] = "✅" if vr["valid"] else "❌"
                biz["email_score"] = str(vr["score"])
            else:
                biz["email_valid"] = ""
                biz["email_score"] = ""

            # WHOIS / domain age
            if options.get("domain_age", False) and biz.get("website",""):
                self.log_cb(f"  🔍 Domain age: {biz.get('name','')[:25]}...", "info")
                wa = get_domain_age(biz["website"])
                if wa.get("age_days") is not None:
                    biz["domain_age"]   = wa["age_label"]
                    biz["domain_created"]= wa.get("created","")
                    biz["registrar"]    = wa.get("registrar","")
                    self.log_cb(
                        f"  📅 Domain: {wa['age_label']} (created {wa.get('created','')})",
                        "success")
                else:
                    biz["domain_age"]    = "Unknown"
                    biz["domain_created"]= ""
                    biz["registrar"]     = ""
                    self.log_cb(f"  ⚠️ Domain age: {wa.get('error','failed')}", "warn")
            else:
                biz["domain_age"] = biz["domain_created"] = biz["registrar"] = ""

            # Lead & Opportunity scoring
            scores = score_lead(biz, position=idx+1)
            biz["lead_score"]  = str(scores["lead_score"])
            biz["opp_score"]   = str(scores["opp_score"])
            biz["lead_tag"]    = scores["tag"]
            biz["gmb_score"]   = scores["gmb_score"]
            biz["gmb_grade"]   = scores["gmb_grade"]

            biz["position"]       = str(idx + 1)
            biz["search_keyword"] = keyword
            biz["search_location"]= location
            biz["scraped_at"]     = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

            final.append(biz)
            self.row_cb(biz)

        self.log_cb(f"✅ Done: {len(final)} enriched leads", "success")
        return final


# ═════════════════════════════════════════════════════════════════════
#  SETTINGS MANAGER
# ═════════════════════════════════════════════════════════════════════
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "leadhunter_settings.json")

def load_settings() -> dict:
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                return json.load(f)
    except: pass
    return {"places_api_key": "", "serp_api_key": "",
            "default_method": "Selenium (Browser)", "theme": "dark"}

def save_settings(d: dict):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(d, f, indent=2)
    except: pass

# ═════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION (CustomTkinter)
# ═════════════════════════════════════════════════════════════════════
class LeadHunterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("LeadHunter Pro v3.0  —  by AunSEO")
        self.geometry("1400x860")
        self.minsize(1100, 700)

        self._settings   = load_settings()
        self._results    = []
        self._running    = False
        self._paused     = False
        self._scraper    = None
        self._city_cache = {}

        # Stat vars
        self._v_total = ctk.StringVar(value="0")
        self._v_email = ctk.StringVar(value="0")
        self._v_new   = ctk.StringVar(value="0")
        self._v_hot   = ctk.StringVar(value="0")
        self._v_phone = ctk.StringVar(value="0")

        self._build_layout()
        self._load_history_tab()

    # ──────────────────────────────────────────────────────────────
    #  LAYOUT
    # ──────────────────────────────────────────────────────────────
    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        self._build_header()

        # Sidebar
        self._sidebar = ctk.CTkScrollableFrame(
            self, width=310, corner_radius=0,
            fg_color=("#f0f0f0","#0D1320"))
        self._sidebar.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        self._build_sidebar()

        # Main area
        self._main = ctk.CTkTabview(self, corner_radius=8)
        self._main.grid(row=1, column=1, sticky="nsew", padx=(6,8), pady=(6,0))

        self._tab_results  = self._main.add("📊  Results")
        self._tab_email    = self._main.add("📧  Bulk Email Finder")
        self._tab_heatmap  = self._main.add("🗺️  Heatmap")
        self._tab_log      = self._main.add("📟  Activity Log")
        self._tab_history  = self._main.add("🕓  History")
        self._tab_settings = self._main.add("⚙️  Settings")

        self._build_results_tab()
        self._build_email_tab()
        self._build_heatmap_tab()
        self._build_log_tab()
        self._build_history_tab()
        self._build_settings_tab()

        # Status bar
        self._build_statusbar()

    # ── Header ───────────────────────────────────────────────────
    def _build_header(self):
        hdr = ctk.CTkFrame(self, height=56, corner_radius=0,
                            fg_color=("#e8f0fe","#0B1422"))
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)

        # Logo
        ctk.CTkLabel(hdr, text="⚡  LeadHunter Pro",
                     font=ctk.CTkFont("Segoe UI", 20, "bold"),
                     text_color=("#1a73e8","#38BDF8")
                     ).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        ctk.CTkLabel(hdr, text="Premium Local Business Intelligence — by AunSEO",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=("gray50","#64748B")
                     ).grid(row=0, column=1, sticky="w")

        # Stats pills
        stats_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        stats_frame.grid(row=0, column=2, padx=16, pady=8, sticky="e")

        stat_data = [
            ("TOTAL",    self._v_total, "#38BDF8"),
            ("EMAIL",    self._v_email, "#34D399"),
            ("NEW",      self._v_new,   "#FBBF24"),
            ("HOT 🔥",   self._v_hot,   "#F87171"),
            ("PHONE",    self._v_phone, "#A78BFA"),
        ]
        for lbl, var, clr in stat_data:
            pill = ctk.CTkFrame(stats_frame, fg_color=("#f8f8f8","#1A2540"),
                                corner_radius=8)
            pill.pack(side="left", padx=3)
            ctk.CTkLabel(pill, text=lbl, font=ctk.CTkFont("Segoe UI",7,"bold"),
                         text_color=clr).pack(padx=10, pady=(4,0))
            ctk.CTkLabel(pill, textvariable=var,
                         font=ctk.CTkFont("Segoe UI",18,"bold"),
                         text_color=clr).pack(padx=10, pady=(0,4))

    # ── Sidebar ──────────────────────────────────────────────────
    def _build_sidebar(self):
        sb = self._sidebar
        pad = {"padx": 12, "pady": 4}

        def section(txt):
            ctk.CTkLabel(sb, text=txt,
                         font=ctk.CTkFont("Segoe UI",10,"bold"),
                         text_color="#38BDF8"
                         ).pack(anchor="w", padx=12, pady=(14,2))
            ctk.CTkFrame(sb, height=1, fg_color="#1E2D45"
                         ).pack(fill="x", padx=12, pady=(0,6))

        def label(txt):
            ctk.CTkLabel(sb, text=txt,
                         font=ctk.CTkFont("Segoe UI",10),
                         text_color=("gray40","#94A3B8")
                         ).pack(anchor="w", padx=14, pady=(4,1))

        # ── Scraping Method ───────────────────────────────────
        section("⚡ SCRAPING METHOD")
        self._method_var = ctk.StringVar(value=self._settings.get(
            "default_method","Selenium (Browser)"))
        ctk.CTkSegmentedButton(
            sb,
            values=["Selenium (Browser)","Raw HTTP (Fast)",
                    "Google Places API","SerpAPI"],
            variable=self._method_var,
            font=ctk.CTkFont("Segoe UI",9),
            height=30,
            command=self._on_method_change,
        ).pack(fill="x", **pad)

        # API key inline frame (shown when API method selected)
        self._api_frame = ctk.CTkFrame(sb, fg_color=("#fff3e0","#1A1200"), corner_radius=6)
        self._api_frame.pack(fill="x", padx=12, pady=(0,4))
        self._api_frame.pack_forget()  # hidden by default

        ctk.CTkLabel(self._api_frame, text="🔑 API Key:",
                     font=ctk.CTkFont("Segoe UI",9,"bold"),
                     text_color=("#e65100","#FCD34D")
                     ).pack(anchor="w", padx=8, pady=(6,2))

        self._inline_api_var = ctk.StringVar(value="")
        self._inline_api_entry = ctk.CTkEntry(
            self._api_frame, textvariable=self._inline_api_var,
            placeholder_text="Paste your API key here...",
            height=32, show="*", font=ctk.CTkFont("Consolas",9))
        self._inline_api_entry.pack(fill="x", padx=8, pady=(0,4))

        ctk.CTkButton(self._api_frame, text="💾 Save Key",
                      height=26, font=ctk.CTkFont("Segoe UI",9,"bold"),
                      fg_color=("#e65100","#D97706"), hover_color=("#bf360c","#B45309"),
                      command=self._save_inline_api_key
                      ).pack(fill="x", padx=8, pady=(0,8))

        # ── Location ─────────────────────────────────────────
        section("📍 LOCATION")

        label("Country")
        self._country_var = ctk.StringVar(value="Pakistan")
        self._country_cb = ctk.CTkComboBox(
            sb, variable=self._country_var,
            values=COUNTRIES, width=280,
            command=self._on_country_change,
            font=ctk.CTkFont("Segoe UI",11))
        self._country_cb.pack(fill="x", **pad)
        # Load default country cities immediately
        self.after(100, lambda: self._on_country_change("Pakistan"))

        label("City / Area")
        self._city_var = ctk.StringVar(value="Karachi")
        self._city_cb  = ctk.CTkComboBox(
            sb, variable=self._city_var,
            values=["Karachi","Lahore","Islamabad"],
            width=280, font=ctk.CTkFont("Segoe UI",11))
        self._city_cb.pack(fill="x", **pad)

        ctk.CTkButton(sb, text="🔄  Refresh city list",
                      height=28, font=ctk.CTkFont("Segoe UI",9),
                      fg_color=("#e8f0fe","#1A2540"),
                      text_color=("#1a73e8","#38BDF8"),
                      hover_color=("#d2e3fc","#243548"),
                      command=lambda: self._on_country_change()
                      ).pack(fill="x", padx=12, pady=(2,4))

        # ── Search ───────────────────────────────────────────
        section("🔍 SEARCH")

        label("Niche / Keyword")
        self._kw_var = ctk.StringVar(value="dental clinic")
        kw_cb = ctk.CTkComboBox(
            sb, variable=self._kw_var, values=NICHES,
            width=280, font=ctk.CTkFont("Segoe UI",11))
        kw_cb.pack(fill="x", **pad)

        label("Max Results")
        max_row = ctk.CTkFrame(sb, fg_color="transparent")
        max_row.pack(fill="x", padx=12, pady=(2,4))

        self._max_var = ctk.StringVar(value="100")
        for v, t in [(50,"50"),(100,"100"),(200,"200"),(500,"500")]:
            ctk.CTkButton(max_row, text=t, width=58, height=28,
                          font=ctk.CTkFont("Segoe UI",9),
                          command=lambda x=v: self._max_var.set(str(x))
                          ).pack(side="left", padx=2)

        self._max_entry = ctk.CTkEntry(
            sb, textvariable=self._max_var,
            width=70, height=30, font=ctk.CTkFont("Segoe UI",10))
        self._max_entry.pack(anchor="e", padx=12, pady=(0,4))

        # ── Filters ──────────────────────────────────────────
        section("🎛  FILTERS")

        label("Minimum Rating  ⭐")
        self._min_rating_val = 0.0   # plain float, no tkinter var
        self._rating_lbl = ctk.CTkLabel(sb, text="Any (0.0+)",
            font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color="#FBBF24")
        self._rating_lbl.pack(anchor="e", padx=14)

        def _on_slider(v):
            self._min_rating_val = round(float(v), 1)
            self._rating_lbl.configure(
                text=f"Min: {self._min_rating_val}⭐" if self._min_rating_val > 0
                else "Any (0.0+)")

        self._rating_slider = ctk.CTkSlider(
            sb, from_=0, to=5, number_of_steps=10,
            command=_on_slider,
            width=260, progress_color="#FBBF24"
        )
        self._rating_slider.set(0)
        self._rating_slider.pack(fill="x", padx=12, pady=(0,6))

        label("Max Reviews (0 = no limit)")
        max_rv_row = ctk.CTkFrame(sb, fg_color="transparent")
        max_rv_row.pack(fill="x", padx=12, pady=(2,4))
        self._max_rv_var = ctk.StringVar(value="0")
        for v, t in [(0,"All"),(10,"≤10"),(25,"≤25"),(100,"≤100")]:
            ctk.CTkButton(max_rv_row, text=t, width=60, height=26,
                          font=ctk.CTkFont("Segoe UI",9),
                          command=lambda x=v: self._max_rv_var.set(str(x))
                          ).pack(side="left", padx=2)

        # Checkboxes
        chk_frame = ctk.CTkFrame(sb, fg_color="transparent")
        chk_frame.pack(fill="x", padx=12, pady=4)

        self._new_only   = ctk.BooleanVar(value=False)
        self._find_email = ctk.BooleanVar(value=True)
        self._verify_em  = ctk.BooleanVar(value=True)
        self._dom_age    = ctk.BooleanVar(value=False)
        self._headless   = ctk.BooleanVar(value=False)

        for var, txt in [
            (self._new_only,   "🆕  New businesses only (≤15 reviews)"),
            (self._find_email, "📧  Find emails from websites"),
            (self._verify_em,  "✅  Verify emails (MX check)"),
            (self._dom_age,    "🗓  Domain age check (RDAP)"),
            (self._headless,   "👻  Run browser in background"),
        ]:
            ctk.CTkCheckBox(chk_frame, text=txt, variable=var,
                            font=ctk.CTkFont("Segoe UI",10)).pack(anchor="w", pady=2)

        # ── Actions ──────────────────────────────────────────
        section("▶ ACTIONS")

        self._start_btn = ctk.CTkButton(
            sb, text="▶   START SCRAPING",
            height=44, font=ctk.CTkFont("Segoe UI",13,"bold"),
            fg_color="#0EA5E9", hover_color="#0284C7",
            command=self._start)
        self._start_btn.pack(fill="x", padx=12, pady=(0,4))

        btns2 = ctk.CTkFrame(sb, fg_color="transparent")
        btns2.pack(fill="x", padx=12, pady=(0,4))

        self._pause_btn = ctk.CTkButton(
            btns2, text="⏸  Pause", height=34, width=130,
            fg_color="#F59E0B", hover_color="#D97706",
            font=ctk.CTkFont("Segoe UI",10,"bold"),
            state="disabled", command=self._toggle_pause)
        self._pause_btn.pack(side="left", padx=(0,4))

        self._stop_btn = ctk.CTkButton(
            btns2, text="⏹  Stop", height=34, width=130,
            fg_color="#EF4444", hover_color="#DC2626",
            font=ctk.CTkFont("Segoe UI",10,"bold"),
            state="disabled", command=self._stop)
        self._stop_btn.pack(side="left")

        # Progress
        self._prog_var = ctk.DoubleVar(value=0)
        ctk.CTkProgressBar(
            sb, variable=self._prog_var,
            width=280, height=8,
            progress_color="#10B981"
        ).pack(fill="x", padx=12, pady=(6,2))

        self._prog_lbl = ctk.CTkLabel(sb, text="0 / 0",
            font=ctk.CTkFont("Consolas",9),
            text_color="#64748B")
        self._prog_lbl.pack(anchor="e", padx=14)

        # Export buttons
        section("💾 EXPORT")

        exp_row1 = ctk.CTkFrame(sb, fg_color="transparent")
        exp_row1.pack(fill="x", padx=12, pady=(0,4))

        ctk.CTkButton(exp_row1, text="📄 CSV", height=32, width=88,
                      fg_color=("#e8f5e9","#1A3020"),
                      text_color=("#1b5e20","#34D399"),
                      hover_color=("#c8e6c9","#1F3D28"),
                      font=ctk.CTkFont("Segoe UI",10,"bold"),
                      command=self._export_csv
                      ).pack(side="left", padx=(0,4))

        ctk.CTkButton(exp_row1, text="📗 Excel", height=32, width=88,
                      fg_color=("#e8f5e9","#1A3020"),
                      text_color=("#1b5e20","#34D399"),
                      hover_color=("#c8e6c9","#1F3D28"),
                      font=ctk.CTkFont("Segoe UI",10,"bold"),
                      command=self._export_excel
                      ).pack(side="left")

        ctk.CTkButton(sb, text="📬  Export Mailwizz Format",
                      height=32, font=ctk.CTkFont("Segoe UI",10,"bold"),
                      fg_color=("#fff3e0","#2D1D00"),
                      text_color=("#e65100","#FCD34D"),
                      hover_color=("#ffe0b2","#3D2800"),
                      command=self._export_mailwizz
                      ).pack(fill="x", padx=12, pady=(0,4))

        ctk.CTkButton(sb, text="🗑  Clear All Results",
                      height=30, font=ctk.CTkFont("Segoe UI",9),
                      fg_color=("gray90","#1A1A2E"),
                      text_color=("gray40","#64748B"),
                      hover_color=("gray80","#2A2A3E"),
                      command=self._clear
                      ).pack(fill="x", padx=12, pady=(0,14))

    # ── Results Tab ─────────────────────────────────────────────
    def _build_results_tab(self):
        t = self._tab_results
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)

        # Toolbar
        tb = ctk.CTkFrame(t, fg_color=("gray92","#111827"), height=42, corner_radius=6)
        tb.grid(row=0, column=0, sticky="ew", pady=(0,4))

        ctk.CTkLabel(tb, text="Filter:",
                     font=ctk.CTkFont("Segoe UI",10)).pack(side="left", padx=(12,4))

        self._filter_var = ctk.StringVar()
        self._filter_var.trace("w", lambda *_: self._refresh_table())
        ctk.CTkEntry(tb, textvariable=self._filter_var,
                     placeholder_text="Search by name, email, phone...",
                     width=220, height=30,
                     font=ctk.CTkFont("Segoe UI",10)
                     ).pack(side="left", padx=4)

        self._show_var = ctk.StringVar(value="all")
        for val, lbl in [("all","All"),("hot","Hot Leads 🔥"),
                          ("new","New Business"),("email","Has Email")]:
            ctk.CTkRadioButton(tb, text=lbl, value=val,
                               variable=self._show_var,
                               font=ctk.CTkFont("Segoe UI",9),
                               command=self._refresh_table
                               ).pack(side="left", padx=6)

        ctk.CTkButton(tb, text="📋 Copy Selected",
                      height=28, width=110,
                      font=ctk.CTkFont("Segoe UI",9),
                      command=self._copy_selected
                      ).pack(side="right", padx=12)

        # Table (using tkinter Treeview inside ctk frame)
        tbl_frame = ctk.CTkFrame(t, fg_color=("white","#0F1623"), corner_radius=6)
        tbl_frame.grid(row=1, column=0, sticky="nsew")
        tbl_frame.grid_rowconfigure(0, weight=1)
        tbl_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Pro.Treeview",
            background="#0F1623", foreground="#E2E8F0",
            fieldbackground="#0F1623", rowheight=26,
            font=("Segoe UI",9), borderwidth=0)
        style.configure("Pro.Treeview.Heading",
            background="#1A2540", foreground="#38BDF8",
            font=("Segoe UI",9,"bold"), relief="flat", borderwidth=0)
        style.map("Pro.Treeview",
            background=[("selected","#1E3A5F")],
            foreground=[("selected","#7DD3FC")])

        cols = ("#","Name","Category","⭐","Reviews","New?","Lead","Opp","GMB",
                "Tag","Phone","Email","✅Email","Website","Address","Scraped")
        self._tree = ttk.Treeview(tbl_frame, columns=cols, selectmode="extended",
                                   show="headings", style="Pro.Treeview")
        self._sort_reverse = {c: False for c in cols}
        widths = [32,200,110,42,65,52,45,45,55,110,120,175,52,150,200,95]
        for col, w in zip(cols, widths):
            self._tree.heading(col, text=col,
                command=lambda c=col: self._sort_column(c))
            anchor = "w" if col in ("Name","Category","Tag","Email","Website","Address") else "center"
            self._tree.column(col, width=w, minwidth=25, stretch=False, anchor=anchor)

        vsb = ttk.Scrollbar(tbl_frame, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tbl_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._tree.tag_configure("hot",    foreground="#F87171", background="#1A0A0A")
        self._tree.tag_configure("warm",   foreground="#FCD34D", background="#1A1500")
        self._tree.tag_configure("new",    foreground="#34D399", background="#0A1A12")
        self._tree.tag_configure("normal", foreground="#E2E8F0")
        self._tree.tag_configure("alt",    background="#0C1520")

        self._tree.bind("<Double-1>", self._on_row_double_click)
        self._tree.bind("<Button-3>", self._on_right_click)

    # ── Bulk Email Finder Tab ───────────────────────────────────
    def _build_email_tab(self):
        t = self._tab_email
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)
        t.grid_columnconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(t, fg_color=("gray92","#111827"), corner_radius=6)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,6))

        ctk.CTkLabel(hdr, text="📧  Bulk Email Finder",
                     font=ctk.CTkFont("Segoe UI",13,"bold"),
                     text_color="#34D399"
                     ).pack(side="left", padx=16, pady=8)
        ctk.CTkLabel(hdr, text="Paste domains → get emails automatically",
                     font=ctk.CTkFont("Segoe UI",9),
                     text_color="#64748B"
                     ).pack(side="left")

        # Left: Input
        left = ctk.CTkFrame(t, fg_color=("white","#0F1623"), corner_radius=8)
        left.grid(row=1, column=0, sticky="nsew", padx=(0,4))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Paste Domains (one per line):",
                     font=ctk.CTkFont("Segoe UI",10,"bold")
                     ).grid(row=0, column=0, sticky="w", padx=12, pady=(10,4))

        self._email_input = ctk.CTkTextbox(
            left, font=ctk.CTkFont("Consolas",10),
            fg_color=("#f8fafc","#080C14"), wrap="none")
        self._email_input.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0,4))
        self._email_input.insert("end", "example.com\ngoogle.com\nyahoo.com\n# One domain per line")

        conc_row = ctk.CTkFrame(left, fg_color="transparent")
        conc_row.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        ctk.CTkLabel(conc_row, text="Threads (speed):",
                     font=ctk.CTkFont("Segoe UI",9)).pack(side="left")
        self._email_threads = ctk.CTkSegmentedButton(
            conc_row, values=["3","5","10","15"],
            font=ctk.CTkFont("Segoe UI",9), width=180)
        self._email_threads.set("5")
        self._email_threads.pack(side="left", padx=8)

        # Buttons
        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=8, pady=(4,10))

        self._email_start_btn = ctk.CTkButton(
            btn_row, text="▶  START FINDING EMAILS",
            height=38, font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color="#10B981", hover_color="#059669",
            command=self._start_bulk_email)
        self._email_start_btn.pack(side="left", fill="x", expand=True, padx=(0,4))

        ctk.CTkButton(btn_row, text="⏹ Stop", height=38, width=80,
                      fg_color="#EF4444", hover_color="#DC2626",
                      font=ctk.CTkFont("Segoe UI",10,"bold"),
                      command=self._stop_bulk_email
                      ).pack(side="left")

        export_row = ctk.CTkFrame(left, fg_color="transparent")
        export_row.grid(row=4, column=0, sticky="ew", padx=8, pady=(0,6))
        ctk.CTkButton(export_row, text="✅ Export Found Only", height=32,
                      fg_color=("#e8f5e9","#1A3020"),
                      text_color=("#1b5e20","#34D399"),
                      font=ctk.CTkFont("Segoe UI",9,"bold"),
                      command=lambda: self._export_email_csv(found_only=True)
                      ).pack(side="left", expand=True, fill="x", padx=(0,3))
        ctk.CTkButton(export_row, text="📄 Export All", height=32,
                      fg_color=("#e8f0fe","#1A2540"),
                      text_color=("#1a73e8","#38BDF8"),
                      font=ctk.CTkFont("Segoe UI",9,"bold"),
                      command=lambda: self._export_email_csv(found_only=False)
                      ).pack(side="left", expand=True, fill="x", padx=(0,3))
        ctk.CTkButton(export_row, text="🗑", height=32, width=36,
                      fg_color=("gray85","#1A1A2E"),
                      text_color=("gray30","#94A3B8"),
                      font=ctk.CTkFont("Segoe UI",9),
                      command=self._clear_email_results
                      ).pack(side="left")

        # Progress
        self._email_prog = ctk.CTkProgressBar(left, height=6, progress_color="#10B981")
        self._email_prog.grid(row=5, column=0, sticky="ew", padx=8, pady=(0,4))
        self._email_prog.set(0)
        self._email_status = ctk.CTkLabel(left, text="Ready",
                                           font=ctk.CTkFont("Segoe UI",9),
                                           text_color="#64748B")
        self._email_status.grid(row=6, column=0, sticky="w", padx=12, pady=(0,8))

        # Right: Results table
        right = ctk.CTkFrame(t, fg_color=("white","#0F1623"), corner_radius=8)
        right.grid(row=1, column=1, sticky="nsew", padx=(4,0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # Stats row
        self._email_stats_lbl = ctk.CTkLabel(right,
            text="Found: 0  |  Not Found: 0  |  Total: 0",
            font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color="#38BDF8")
        self._email_stats_lbl.grid(row=0, column=0, columnspan=2,
                                    sticky="w", padx=12, pady=(10,4))

        email_cols = ("Domain","Email","✅","Score","Status")
        self._email_tree = ttk.Treeview(right, columns=email_cols,
                                         show="headings", style="Pro.Treeview",
                                         selectmode="extended")
        email_widths = [200, 260, 40, 50, 90]
        for col, w in zip(email_cols, email_widths):
            self._email_tree.heading(col, text=col)
            self._email_tree.column(col, width=w, minwidth=30)

        em_vsb = ttk.Scrollbar(right, orient="vertical",
                                command=self._email_tree.yview)
        self._email_tree.configure(yscrollcommand=em_vsb.set)
        self._email_tree.grid(row=1, column=0, sticky="nsew", padx=(8,0), pady=(0,8))
        em_vsb.grid(row=1, column=1, sticky="ns", pady=(0,8))

        self._email_tree.tag_configure("found",    foreground="#34D399")
        self._email_tree.tag_configure("notfound", foreground="#64748B")
        self._email_tree.tag_configure("error",    foreground="#F87171")

        # Right-click copy
        self._email_tree.bind("<Button-3>", self._email_right_click)

        self._email_results = []
        self._email_stop    = False

    # ── Heatmap Tab ──────────────────────────────────────────────
    def _build_heatmap_tab(self):
        t = self._tab_heatmap
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(1, weight=1)

        # Left controls
        ctrl = ctk.CTkScrollableFrame(t, width=300,
                                       fg_color=("gray92","#0D1320"), corner_radius=8)
        ctrl.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0,6))

        def hmap_section(txt):
            ctk.CTkLabel(ctrl, text=txt,
                         font=ctk.CTkFont("Segoe UI",10,"bold"),
                         text_color="#F59E0B"
                         ).pack(anchor="w", padx=12, pady=(14,2))
            ctk.CTkFrame(ctrl, height=1, fg_color="#1E2D45"
                         ).pack(fill="x", padx=12, pady=(0,6))

        def hmap_label(txt):
            ctk.CTkLabel(ctrl, text=txt,
                         font=ctk.CTkFont("Segoe UI",9),
                         text_color="#94A3B8"
                         ).pack(anchor="w", padx=14, pady=(2,1))

        # Target Business
        hmap_section("🏢 TARGET BUSINESS")
        hmap_label("Business Name (exact)")
        self._hmap_biz = ctk.CTkEntry(ctrl, placeholder_text="e.g. Boston Family Dental",
                                       height=32, font=ctk.CTkFont("Segoe UI",10))
        self._hmap_biz.pack(fill="x", padx=12, pady=(0,6))

        hmap_label("City / Location")
        self._hmap_city = ctk.CTkEntry(ctrl, placeholder_text="e.g. Boston, MA",
                                        height=32, font=ctk.CTkFont("Segoe UI",10))
        self._hmap_city.pack(fill="x", padx=12, pady=(0,6))

        hmap_label("Keyword to track")
        self._hmap_kw = ctk.CTkEntry(ctrl, placeholder_text="e.g. dental clinic",
                                      height=32, font=ctk.CTkFont("Segoe UI",10))
        self._hmap_kw.pack(fill="x", padx=12, pady=(0,6))

        # Grid Settings
        hmap_section("⚙️ GRID SETTINGS")
        hmap_label("Grid Size")
        self._hmap_grid = ctk.CTkSegmentedButton(
            ctrl, values=["3×3","5×5","7×7"],
            font=ctk.CTkFont("Segoe UI",9))
        self._hmap_grid.set("5×5")
        self._hmap_grid.pack(fill="x", padx=12, pady=(0,8))

        hmap_label("Radius (km)")
        self._hmap_radius_val = 2.0
        self._hmap_radius_lbl = ctk.CTkLabel(ctrl, text="2.0 km",
            font=ctk.CTkFont("Segoe UI",10,"bold"), text_color="#F59E0B")
        self._hmap_radius_lbl.pack(anchor="e", padx=14)

        def _on_radius(v):
            self._hmap_radius_val = round(float(v), 1)
            self._hmap_radius_lbl.configure(text=f"{self._hmap_radius_val} km")

        ctk.CTkSlider(ctrl, from_=0.5, to=10, number_of_steps=19,
                      command=_on_radius, progress_color="#F59E0B"
                      ).pack(fill="x", padx=12, pady=(0,8))

        # Method
        hmap_section("⚡ METHOD")
        self._hmap_method = ctk.CTkSegmentedButton(
            ctrl, values=["Selenium (Free)","Google Places API"],
            font=ctk.CTkFont("Segoe UI",9))
        self._hmap_method.set("Selenium (Free)")
        self._hmap_method.pack(fill="x", padx=12, pady=(0,8))

        # Info box
        self._hmap_info = ctk.CTkLabel(ctrl,
            text="Selenium: Free, slower | Places API: Fast, needs key",
            font=ctk.CTkFont("Segoe UI",8),
            text_color="#475569", justify="left")
        self._hmap_info.pack(anchor="w", padx=14, pady=(0,8))

        # Actions
        hmap_section("▶ GENERATE")
        self._hmap_start_btn = ctk.CTkButton(
            ctrl, text="🗺️  GENERATE HEATMAP",
            height=40, font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color="#F59E0B", hover_color="#D97706",
            text_color="#000000",
            command=self._start_heatmap)
        self._hmap_start_btn.pack(fill="x", padx=12, pady=(0,4))

        ctk.CTkButton(ctrl, text="⏹ Stop", height=32,
                      fg_color="#EF4444", hover_color="#DC2626",
                      font=ctk.CTkFont("Segoe UI",10),
                      command=self._stop_heatmap
                      ).pack(fill="x", padx=12, pady=(0,4))

        ctk.CTkButton(ctrl, text="🌐  Open Last Heatmap",
                      height=32, fg_color=("#e8f0fe","#1A2540"),
                      text_color=("#1a73e8","#38BDF8"),
                      font=ctk.CTkFont("Segoe UI",10),
                      command=self._open_heatmap
                      ).pack(fill="x", padx=12, pady=(0,8))

        # Progress
        self._hmap_prog = ctk.CTkProgressBar(ctrl, height=6, progress_color="#F59E0B")
        self._hmap_prog.pack(fill="x", padx=12, pady=(0,4))
        self._hmap_prog.set(0)

        self._hmap_status = ctk.CTkLabel(ctrl, text="Ready",
                                          font=ctk.CTkFont("Segoe UI",9),
                                          text_color="#64748B")
        self._hmap_status.pack(anchor="w", padx=14, pady=(0,4))

        # Right: Live log
        right = ctk.CTkFrame(t, fg_color=("white","#0F1623"), corner_radius=8)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        top_row = ctk.CTkFrame(right, fg_color=("gray92","#111827"), corner_radius=6)
        top_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,4))
        ctk.CTkLabel(top_row, text="📟  Heatmap Progress Log",
                     font=ctk.CTkFont("Segoe UI",11,"bold")
                     ).pack(side="left", padx=12, pady=6)
        self._hmap_counter = ctk.CTkLabel(top_row, text="0 / 0 points",
                                           font=ctk.CTkFont("Segoe UI",10,"bold"),
                                           text_color="#F59E0B")
        self._hmap_counter.pack(side="right", padx=12)

        self._hmap_log = tk.Text(
            right, bg="#080C14", fg="#94A3B8",
            font=("Consolas",9), relief="flat", bd=0,
            wrap="word", state="disabled")
        hmap_sb = ttk.Scrollbar(right, orient="vertical", command=self._hmap_log.yview)
        self._hmap_log.configure(yscrollcommand=hmap_sb.set)
        self._hmap_log.grid(row=1, column=0, sticky="nsew", padx=(8,0), pady=(0,8))
        hmap_sb.grid(row=1, column=1, sticky="ns", pady=(0,8))

        self._hmap_log.tag_configure("success", foreground="#34D399")
        self._hmap_log.tag_configure("warn",    foreground="#FBBF24")
        self._hmap_log.tag_configure("error",   foreground="#F87171")
        self._hmap_log.tag_configure("info",    foreground="#94A3B8")
        self._hmap_log.tag_configure("dim",     foreground="#334155")

        self._hmap_file = None
        self._hmap_stop = False

    # ── Log Tab ─────────────────────────────────────────────────
    def _build_log_tab(self):
        t = self._tab_log
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)

        tb = ctk.CTkFrame(t, fg_color=("gray92","#111827"), height=40, corner_radius=6)
        tb.grid(row=0, column=0, sticky="ew", pady=(0,4))
        ctk.CTkLabel(tb, text="📟  Live Activity Console",
                     font=ctk.CTkFont("Segoe UI",11,"bold")).pack(side="left", padx=12)
        ctk.CTkButton(tb, text="Clear", width=60, height=26,
                      font=ctk.CTkFont("Segoe UI",9),
                      command=lambda: [self._log_text.configure(state="normal"),
                                       self._log_text.delete("1.0","end"),
                                       self._log_text.configure(state="disabled")]
                      ).pack(side="right", padx=12)

        self._log_text = tk.Text(
            t, bg="#080C14", fg="#94A3B8",
            font=("Consolas",9), relief="flat", bd=0,
            wrap="word", state="disabled",
            insertbackground="#38BDF8")
        log_sb = ttk.Scrollbar(t, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_sb.set)
        log_sb.grid(row=1, column=1, sticky="ns")
        self._log_text.grid(row=1, column=0, sticky="nsew", padx=(4,0))

        self._log_text.tag_configure("success", foreground="#34D399")
        self._log_text.tag_configure("warn",    foreground="#FBBF24")
        self._log_text.tag_configure("error",   foreground="#F87171")
        self._log_text.tag_configure("info",    foreground="#94A3B8")
        self._log_text.tag_configure("dim",     foreground="#334155")

    # ── History Tab ─────────────────────────────────────────────
    def _build_history_tab(self):
        t = self._tab_history
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(t, text="Search History",
                     font=ctk.CTkFont("Segoe UI",13,"bold")
                     ).grid(row=0, column=0, sticky="w", padx=12, pady=8)

        hf = ctk.CTkFrame(t, fg_color=("white","#0F1623"), corner_radius=6)
        hf.grid(row=1, column=0, sticky="nsew")
        hf.grid_rowconfigure(0, weight=1)
        hf.grid_columnconfigure(0, weight=1)

        cols = ("Date","Method","Keyword","Location","Leads","Hot","Email")
        self._hist_tree = ttk.Treeview(hf, columns=cols,
                                        show="headings", style="Pro.Treeview")
        widths2 = [130,140,180,140,70,70,80]
        for col, w in zip(cols, widths2):
            self._hist_tree.heading(col, text=col)
            self._hist_tree.column(col, width=w, minwidth=40)

        vsb2 = ttk.Scrollbar(hf, orient="vertical", command=self._hist_tree.yview)
        self._hist_tree.configure(yscrollcommand=vsb2.set)
        self._hist_tree.grid(row=0, column=0, sticky="nsew")
        vsb2.grid(row=0, column=1, sticky="ns")

    # ── Settings Tab ────────────────────────────────────────────
    def _build_settings_tab(self):
        t = self._tab_settings
        t.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(t, text="API Keys & Settings",
                     font=ctk.CTkFont("Segoe UI",14,"bold")
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(16,8))

        # Google Places API
        ctk.CTkLabel(t, text="Google Places API Key",
                     font=ctk.CTkFont("Segoe UI",11,"bold"),
                     text_color="#38BDF8"
                     ).grid(row=1, column=0, sticky="w", padx=16, pady=(12,2))
        ctk.CTkLabel(t, text="Free $200 credit/month • Get at: console.cloud.google.com",
                     font=ctk.CTkFont("Segoe UI",9),
                     text_color="#64748B"
                     ).grid(row=2, column=0, sticky="w", padx=16)

        self._places_key = ctk.CTkEntry(t, placeholder_text="AIza...",
                                         width=500, height=36, show="*",
                                         font=ctk.CTkFont("Consolas",10))
        self._places_key.insert(0, self._settings.get("places_api_key",""))
        self._places_key.grid(row=3, column=0, sticky="w", padx=16, pady=(4,8))

        # SerpAPI
        ctk.CTkLabel(t, text="SerpAPI Key",
                     font=ctk.CTkFont("Segoe UI",11,"bold"),
                     text_color="#38BDF8"
                     ).grid(row=4, column=0, sticky="w", padx=16, pady=(8,2))
        ctk.CTkLabel(t, text="$50/month for 5000 searches • Get at: serpapi.com",
                     font=ctk.CTkFont("Segoe UI",9),
                     text_color="#64748B"
                     ).grid(row=5, column=0, sticky="w", padx=16)

        self._serp_key = ctk.CTkEntry(t, placeholder_text="Your SerpAPI key...",
                                       width=500, height=36, show="*",
                                       font=ctk.CTkFont("Consolas",10))
        self._serp_key.insert(0, self._settings.get("serp_api_key",""))
        self._serp_key.grid(row=6, column=0, sticky="w", padx=16, pady=(4,8))

        ctk.CTkButton(t, text="💾  Save API Keys",
                      width=180, height=36,
                      font=ctk.CTkFont("Segoe UI",11,"bold"),
                      command=self._save_api_keys
                      ).grid(row=7, column=0, sticky="w", padx=16, pady=8)

        # Info box
        info = ctk.CTkTextbox(t, width=600, height=200,
                               font=ctk.CTkFont("Segoe UI",10),
                               fg_color=("#f8fafc","#0F1623"))
        info.grid(row=8, column=0, sticky="w", padx=16, pady=12)
        info.insert("end", """METHOD GUIDE:

Selenium (Browser)  — FREE. Opens real Chrome window. Most compatible.
                      Best for general use. Slower but reliable.

Raw HTTP (Fast)     — FREE. No browser needed. Fast but may get limited results.
                      Good for quick searches.

Google Places API   — OFFICIAL. 100% reliable. 5000 free requests/month.
                      Needs: console.cloud.google.com → Enable Places API → Get key
                      Best for production/SaaS use.

SerpAPI             — PAID ($50/mo). No setup needed, just API key.
                      Fastest, most complete data. Good for high volume.

RECOMMENDATION: Start with Selenium. When scaling to SaaS → use Google Places API.""")
        info.configure(state="disabled")

    # ── Status Bar ───────────────────────────────────────────────
    def _build_statusbar(self):
        sb = ctk.CTkFrame(self, height=32, corner_radius=0,
                           fg_color=("#e2e8f0","#0D1320"))
        sb.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._status_var = ctk.StringVar(
            value="Ready — Configure your search and click START SCRAPING")
        ctk.CTkLabel(sb, textvariable=self._status_var,
                     font=ctk.CTkFont("Segoe UI",9),
                     text_color=("gray40","#38BDF8")
                     ).pack(side="left", padx=12, pady=4)
        ctk.CTkLabel(sb, text="LeadHunter Pro v3.0 — AunSEO",
                     font=ctk.CTkFont("Segoe UI",9),
                     text_color="gray50"
                     ).pack(side="right", padx=12)

    # ══════════════════════════════════════════════════════════════
    #  CITY AUTO-FETCH
    # ══════════════════════════════════════════════════════════════
    def _on_method_change(self, val):
        """Show/hide API key input when API method selected"""
        api_methods = ["Google Places API", "SerpAPI"]
        if val in api_methods:
            # Check if key already saved
            key_setting = "places_api_key" if val == "Google Places API" else "serp_api_key"
            existing = self._settings.get(key_setting, "")
            if existing:
                self._inline_api_var.set(existing)
                self._status_var.set(f"✅ {val} key loaded. Ready to scrape.")
            else:
                self._inline_api_var.set("")
                if val == "Google Places API":
                    self._inline_api_entry.configure(
                        placeholder_text="AIza... (console.cloud.google.com)")
                else:
                    self._inline_api_entry.configure(
                        placeholder_text="Get key at serpapi.com")
                self._status_var.set(f"⚠️ Enter your {val} key below then click Save")
            self._api_frame.pack(fill="x", padx=12, pady=(0,4))
        else:
            self._api_frame.pack_forget()
            self._status_var.set(f"Method: {val}. Configure search and click START.")

    def _save_inline_api_key(self):
        key = self._inline_api_var.get().strip()
        if not key:
            messagebox.showwarning("Empty", "Please enter your API key"); return
        method = self._method_var.get()
        if method == "Google Places API":
            self._settings["places_api_key"] = key
            self._places_key.delete(0, "end")
            self._places_key.insert(0, key)
        else:
            self._settings["serp_api_key"] = key
            self._serp_key.delete(0, "end")
            self._serp_key.insert(0, key)
        save_settings(self._settings)
        self._status_var.set(f"✅ {method} key saved! Click START SCRAPING.")

    def _on_country_change(self, val=None):
        if val is None:
            val = self._country_var.get()
        # Load from hardcoded dict instantly
        cities = COUNTRY_CITIES.get(val, [])
        if cities:
            self._city_cb.configure(values=cities)
            self._city_var.set(cities[0])
            self._city_cb.set(cities[0])
            self._status_var.set(f"✅ {len(cities)} cities loaded for {val}")
        else:
            # For unlisted countries, try online in background
            self._status_var.set(f"🔄 Fetching cities for {val}...")
            threading.Thread(target=self._fetch_cities_bg, args=(val,), daemon=True).start()

    def _fetch_cities_bg(self, country):
        cities = fetch_cities(country)
        if cities:
            self.after(0, self._city_cb.configure, {"values": cities})
            self.after(0, self._city_var.set, cities[0])
            self.after(0, self._city_cb.set, cities[0])
            self.after(0, self._status_var.set, f"✅ {len(cities)} cities for {country}")
        else:
            self.after(0, self._status_var.set, f"⚠️ No cities found — type manually")

    def _fetch_cities(self):
        """Legacy wrapper — calls new method"""
        self._on_country_change()

    # ══════════════════════════════════════════════════════════════
    #  SCRAPER ACTIONS
    # ══════════════════════════════════════════════════════════════
    def _start(self):
        if self._running:
            messagebox.showwarning("Running","Stop current scrape first"); return
        kw   = self._kw_var.get().strip()
        city = self._city_var.get().strip()
        if not kw:   messagebox.showwarning("Missing","Enter a keyword/niche"); return
        if not city: messagebox.showwarning("Missing","Enter a city"); return

        method = self._method_var.get()
        # Check/load API keys from inline entry or settings
        if method == "Google Places API":
            inline_key = self._inline_api_var.get().strip()
            if inline_key:
                self._settings["places_api_key"] = inline_key
                save_settings(self._settings)
            if not self._settings.get("places_api_key"):
                messagebox.showerror("No API Key",
                    "Enter your Google Places API key in the key field above!\n"
                    "Get free key at: console.cloud.google.com"); return
        if method == "SerpAPI":
            inline_key = self._inline_api_var.get().strip()
            if inline_key:
                self._settings["serp_api_key"] = inline_key
                save_settings(self._settings)
            if not self._settings.get("serp_api_key"):
                messagebox.showerror("No API Key",
                    "Enter your SerpAPI key in the key field above!\n"
                    "Get key at: serpapi.com"); return

        self._running = True
        self._paused  = False
        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal")
        self._stop_btn.configure(state="normal")
        self._prog_var.set(0)

        try:
            max_results_val = int(self._max_var.get() or "100")
        except: max_results_val = 100
        try:
            max_rv_val = int(self._max_rv_var.get() or "0")
        except: max_rv_val = 0
        try:
            min_rat_val = float(self._min_rating_val)
        except: min_rat_val = 0.0

        options = {
            "min_rating":   min_rat_val,
            "max_reviews":  max_rv_val,
            "new_only":     self._new_only.get(),
            "find_emails":  self._find_email.get(),
            "verify_emails":self._verify_em.get(),
            "domain_age":   self._dom_age.get(),
            "headless":     self._headless.get(),
        }
        method_key = {
            "Selenium (Browser)":  "Selenium",
            "Raw HTTP (Fast)":     "Raw HTTP (Fast)",
            "Google Places API":   "Google Places API",
            "SerpAPI":             "SerpAPI",
        }.get(method, "Selenium")

        def run():
            scraper = MasterScraper(
                log_cb      = lambda m,t="info": self.after(0, self._log, m, t),
                row_cb      = lambda r: self.after(0, self._add_row, r),
                status_cb   = lambda m: self.after(0, self._status_var.set, m),
                progress_cb = lambda c,t: self.after(0, self._on_progress, c, t),
            )
            self._scraper = scraper
            results = scraper.run(
                method        = method_key,
                keyword       = kw,
                location      = city,
                max_results   = max_results_val,
                options       = options,
                places_api_key= self._settings.get("places_api_key",""),
                serp_api_key  = self._settings.get("serp_api_key",""),
            )
            self._save_history(method_key, kw, city, results)
            self.after(0, self._on_done, len(results))

        threading.Thread(target=run, daemon=True).start()

    def _toggle_pause(self):
        if not self._scraper: return
        if self._paused:
            self._scraper.resume(); self._paused = False
            self._pause_btn.configure(text="⏸  Pause")
            self._status_var.set("▶ Resumed.")
        else:
            self._scraper.pause(); self._paused = True
            self._pause_btn.configure(text="▶  Resume")
            self._status_var.set("⏸ Paused — click Resume to continue.")

    def _stop(self):
        if self._scraper: self._scraper.stop()
        self._status_var.set("⏹ Stopping...")

    def _on_done(self, count):
        self._running = False
        self._paused  = False
        self._start_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled", text="⏸  Pause")
        self._stop_btn.configure(state="disabled")
        self._status_var.set(f"✅ Done — {count} leads extracted. Ready.")
        self._prog_var.set(100)
        self._refresh_table()

    def _on_progress(self, current, total):
        pct = (current / total * 100) if total else 0
        self._prog_var.set(min(100, pct))
        self._prog_lbl.configure(text=f"{current} / {total}")

    # ══════════════════════════════════════════════════════════════
    #  ROW & TABLE
    # ══════════════════════════════════════════════════════════════
    def _add_row(self, biz):
        self._results.append(biz)
        self._insert_row(biz, len(self._results))
        self._update_stats()

    def _insert_row(self, biz, idx):
        tag_str = biz.get("lead_tag","")
        if "HOT"      in tag_str: tag = "hot"
        elif "WARM"   in tag_str: tag = "warm"
        elif "YES"    in str(biz.get("is_new","")): tag = "new"
        elif idx % 2 == 0:        tag = "alt"
        else:                     tag = "normal"

        self._tree.insert("", "end", values=(
            idx,
            biz.get("name",""),
            biz.get("category",""),
            biz.get("rating",""),
            biz.get("reviews",""),
            biz.get("is_new",""),
            biz.get("lead_score",""),
            biz.get("opp_score",""),
            biz.get("gmb_score",""),
            biz.get("lead_tag",""),
            biz.get("phone",""),
            biz.get("email",""),
            biz.get("email_valid",""),
            biz.get("website","")[:35] if biz.get("website") else "",
            biz.get("address",""),
            biz.get("scraped_at",""),
        ), tags=(tag,))
        self._tree.yview_moveto(1)

    def _sort_column(self, col):
        """Sort results table by clicked column."""
        col_map = {
            "#":        "position",
            "Name":     "name",
            "Category": "category",
            "⭐":       "rating",
            "Reviews":  "reviews",
            "New?":     "is_new",
            "Lead":     "lead_score",
            "Opp":      "opp_score",
            "GMB":      "gmb_score",
            "Tag":      "lead_tag",
            "Phone":    "phone",
            "Email":    "email",
            "✅Email":  "email_valid",
            "Website":  "website",
            "Address":  "address",
            "Scraped":  "scraped_at",
        }
        key = col_map.get(col, col.lower())
        rev = self._sort_reverse.get(col, False)

        def sort_key(r):
            val = r.get(key, "")
            # Numeric columns — sort as number
            if key in ("reviews","lead_score","opp_score","position","email_score"):
                try: return float(str(val).replace(",","") or 0)
                except: return 0.0
            if key == "rating":
                try: return float(str(val) or 0)
                except: return 0.0
            if key == "gmb_score":
                try: return int(str(val).split("/")[0] or 0)
                except: return 0
            return str(val).lower()

        self._results.sort(key=sort_key, reverse=rev)
        self._sort_reverse[col] = not rev

        # Update heading arrow
        for c in self._sort_reverse:
            arrow = ""
            if c == col:
                arrow = " ↓" if rev else " ↑"
            self._tree.heading(c, text=c + arrow,
                               command=lambda x=c: self._sort_column(x))

        self._refresh_table()

    def _refresh_table(self, *_):
        for item in self._tree.get_children():
            self._tree.delete(item)
        search = self._filter_var.get().lower()
        show   = self._show_var.get()
        shown  = 0
        for i, biz in enumerate(self._results):
            tag_str = biz.get("lead_tag","")
            is_hot  = "HOT" in tag_str
            is_new  = "YES" in str(biz.get("is_new",""))
            has_em  = bool(biz.get("email",""))
            if show == "hot"   and not is_hot:  continue
            if show == "new"   and not is_new:  continue
            if show == "email" and not has_em:  continue
            if search:
                haystack = " ".join(str(v) for v in biz.values()).lower()
                if search not in haystack: continue
            shown += 1
            self._insert_row(biz, i+1)

    def _update_stats(self):
        r = self._results
        self._v_total.set(str(len(r)))
        self._v_email.set(str(sum(1 for x in r if x.get("email",""))))
        self._v_new.set(str(sum(1 for x in r if "YES" in str(x.get("is_new","")))))
        self._v_hot.set(str(sum(1 for x in r if "HOT" in str(x.get("lead_tag","")))))
        self._v_phone.set(str(sum(1 for x in r if x.get("phone",""))))

    def _on_row_double_click(self, event):
        """Show full detail popup on double-click"""
        item = self._tree.identify_row(event.y)
        if not item: return
        sel = self._tree.selection()
        if not sel: return
        row_idx = self._tree.index(sel[0])
        if row_idx >= len(self._results): return
        biz = self._results[row_idx]

        popup = ctk.CTkToplevel(self)
        popup.title(f"Details: {biz.get('name','')}")
        popup.geometry("600x500")

        tb = ctk.CTkTextbox(popup, font=ctk.CTkFont("Consolas",10),
                             width=560, height=440)
        tb.pack(padx=16, pady=16, fill="both", expand=True)

        for k, v in biz.items():
            if v:
                tb.insert("end", f"{k:20}: {v}\n")
        tb.configure(state="disabled")

        ctk.CTkButton(popup, text="📋 Copy All",
                      command=lambda: [self.clipboard_clear(),
                                       self.clipboard_append(
                                           "\n".join(f"{k}: {v}" for k,v in biz.items() if v)
                                       )]
                      ).pack(pady=4)

    def _on_right_click(self, event):
        """Right-click context menu"""
        # Select the row that was right-clicked
        item = self._tree.identify_row(event.y)
        if item:
            self._tree.selection_set(item)
        sel = self._tree.selection()
        if not sel: return
        row_idx = self._tree.index(sel[0])
        if row_idx >= len(self._results): return
        biz = self._results[row_idx]

        menu = tk.Menu(self, tearoff=0,
                            bg="#1A2540", fg="#E2E8F0",
                            activebackground="#243548",
                            font=("Segoe UI",9))
        menu.add_command(label=f"📋 Copy Name",
                         command=lambda: self._copy_text(biz.get("name","")))
        menu.add_command(label=f"📧 Copy Email",
                         command=lambda: self._copy_text(biz.get("email","")))
        menu.add_command(label=f"📞 Copy Phone",
                         command=lambda: self._copy_text(biz.get("phone","")))
        menu.add_command(label=f"🌐 Copy Website",
                         command=lambda: self._copy_text(biz.get("website","")))
        menu.add_separator()
        menu.add_command(label="📋 Copy Full Row (CSV)",
                         command=lambda: self._copy_text(
                             ",".join(f'"{v}"' for v in biz.values())))
        menu.post(event.x_root, event.y_root)

    def _copy_selected(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected","Click a row first"); return
        rows = []
        for item in sel:
            vals = self._tree.item(item,"values")
            rows.append(",".join(f'"{v}"' for v in vals))
        self._copy_text("\n".join(rows))
        messagebox.showinfo("Copied",f"{len(rows)} row(s) copied to clipboard")

    def _copy_text(self, txt):
        self.clipboard_clear()
        self.clipboard_append(str(txt))

    # ══════════════════════════════════════════════════════════════
    #  LOG
    # ══════════════════════════════════════════════════════════════
    def _log(self, msg: str, tag: str = "info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_text.configure(state="normal")
        self._log_text.insert("end", f"[{ts}] ", "dim")
        self._log_text.insert("end", f"{msg}\n", tag)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ══════════════════════════════════════════════════════════════
    #  HISTORY
    # ══════════════════════════════════════════════════════════════
    HIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__) if "__file__" in dir() else "."),
                              "leadhunter_v3_history.json")

    def _save_history(self, method, kw, city, results):
        hot   = sum(1 for r in results if "HOT" in str(r.get("lead_tag","")))
        email = sum(1 for r in results if r.get("email",""))
        entry = {
            "date":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "method":   method,
            "keyword":  kw,
            "location": city,
            "leads":    len(results),
            "hot":      hot,
            "email":    email,
        }
        try:
            records = []
            if os.path.exists(self.HIST_FILE):
                with open(self.HIST_FILE) as f:
                    records = json.load(f)
            records.insert(0, entry)
            records = records[:100]
            with open(self.HIST_FILE,"w") as f:
                json.dump(records, f, indent=2)
        except: pass
        self.after(0, self._load_history_tab)

    def _load_history_tab(self):
        for item in self._hist_tree.get_children():
            self._hist_tree.delete(item)
        try:
            if os.path.exists(self.HIST_FILE):
                with open(self.HIST_FILE) as f:
                    records = json.load(f)
                for rec in records:
                    self._hist_tree.insert("", "end", values=(
                        rec.get("date",""), rec.get("method",""),
                        rec.get("keyword",""), rec.get("location",""),
                        rec.get("leads",""), rec.get("hot",""),
                        rec.get("email",""),
                    ))
        except: pass

    # ══════════════════════════════════════════════════════════════
    #  API KEYS SAVE
    # ══════════════════════════════════════════════════════════════
    def _save_api_keys(self):
        self._settings["places_api_key"] = self._places_key.get().strip()
        self._settings["serp_api_key"]   = self._serp_key.get().strip()
        save_settings(self._settings)
        messagebox.showinfo("Saved ✅","API keys saved successfully!")

    # ══════════════════════════════════════════════════════════════
    #  EXPORT
    # ══════════════════════════════════════════════════════════════
    def _get_save_path(self, ext, name="leads"):
        return filedialog.asksaveasfilename(
            defaultextension=f".{ext}",
            filetypes=[(f"{ext.upper()} files", f"*.{ext}"),("All","*.*")],
            initialfile=f"{name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"
        )

    EXPORT_KEYS = [
        "name","category","rating","reviews","is_new","lead_score","opp_score",
        "gmb_score","gmb_grade","lead_tag","phone","email","email_valid",
        "email_score","website","address","hours","domain_age","domain_created",
        "search_keyword","search_location","scraped_at","source"
    ]

    def _export_csv(self):
        if not self._results:
            messagebox.showinfo("Empty","No data"); return
        path = self._get_save_path("csv")
        if not path: return
        with open(path,"w",newline="",encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=self.EXPORT_KEYS, extrasaction="ignore")
            w.writeheader(); w.writerows(self._results)
        messagebox.showinfo("✅ Saved",f"{len(self._results)} leads → {path}")

    def _export_excel(self):
        if not self._results:
            messagebox.showinfo("Empty","No data"); return
        path = self._get_save_path("xlsx")
        if not path: return
        try:
            import openpyxl
            from openpyxl.styles import PatternFill, Font, Alignment
            from openpyxl.utils import get_column_letter

            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Leads"
            headers = [k.replace("_"," ").title() for k in self.EXPORT_KEYS]

            hf  = PatternFill("solid", fgColor="1A2540")
            hfn = Font(bold=True, color="38BDF8", name="Segoe UI", size=9)
            for ci, h in enumerate(headers,1):
                c = ws.cell(row=1,column=ci,value=h)
                c.fill=hf; c.font=hfn
                c.alignment=Alignment(horizontal="center")

            hot_fill  = PatternFill("solid", fgColor="1A0808")
            new_fill  = PatternFill("solid", fgColor="081A10")
            norm_fill = PatternFill("solid", fgColor="0F1623")
            alt_fill  = PatternFill("solid", fgColor="080C14")
            hot_font  = Font(color="F87171", name="Segoe UI", size=9)
            new_font  = Font(color="34D399", name="Segoe UI", size=9)
            norm_font = Font(color="E2E8F0", name="Segoe UI", size=9)

            for ri,row in enumerate(self._results,2):
                is_hot = "HOT" in str(row.get("lead_tag",""))
                is_new = "YES" in str(row.get("is_new",""))
                fill = hot_fill if is_hot else (new_fill if is_new else
                        (norm_fill if ri%2==0 else alt_fill))
                font = hot_font if is_hot else (new_font if is_new else norm_font)
                for ci,k in enumerate(self.EXPORT_KEYS,1):
                    c = ws.cell(row=ri,column=ci,value=row.get(k,""))
                    c.fill=fill; c.font=font

            col_widths = [25,15,7,8,8,7,7,8,10,12,16,28,7,7,30,30,20,10,10,20,20,16,10]
            for ci,w in enumerate(col_widths,1):
                ws.column_dimensions[get_column_letter(ci)].width=w
            ws.freeze_panes="A2"
            wb.save(path)
            messagebox.showinfo("✅ Saved",f"Excel: {len(self._results)} leads → {path}")
        except Exception as e:
            messagebox.showerror("Error",str(e))

    def _export_mailwizz(self):
        if not self._results:
            messagebox.showinfo("Empty","No data"); return
        path = self._get_save_path("csv","mailwizz_import")
        if not path: return
        mw = {"EMAIL":"email","FNAME":"name","PHONE":"phone",
              "CITY":"search_location","CATEGORY":"category",
              "WEBSITE":"website","LEAD_SCORE":"lead_score",
              "IS_NEW":"is_new","RATING":"rating","ADDRESS":"address"}
        rows = [r for r in self._results if r.get("email","")]
        if not rows:
            if not messagebox.askyesno("No Emails","No emails found. Export all anyway?"):
                return
            rows = self._results
        with open(path,"w",newline="",encoding="utf-8-sig") as f:
            w = csv.DictWriter(f,fieldnames=list(mw.keys()),extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({mk:r.get(sk,"") for mk,sk in mw.items()})
        messagebox.showinfo("✅ Mailwizz Export",
            f"{len(rows)} leads exported\n{path}\n\n"
            "Import: Mailwizz → Lists → Import subscribers")

    # ══════════════════════════════════════════════════════════════
    #  BULK EMAIL FINDER ACTIONS
    # ══════════════════════════════════════════════════════════════
    def _start_bulk_email(self):
        raw = self._email_input.get("1.0","end").strip()
        lines = [l.strip() for l in raw.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        if not lines:
            messagebox.showwarning("Empty","Paste some domains first"); return

        self._email_stop    = False
        self._email_results = []
        self._email_start_btn.configure(state="disabled")
        self._email_status.configure(text=f"Starting... {len(lines)} domains")
        self._email_prog.set(0)

        for item in self._email_tree.get_children():
            self._email_tree.delete(item)

        threads = int(self._email_threads.get())

        def run():
            total   = len(lines)
            done    = 0
            found   = 0
            not_fnd = 0

            with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
                futures = {ex.submit(bulk_find_email, d): d for d in lines}
                for fut in concurrent.futures.as_completed(futures):
                    if self._email_stop:
                        ex.shutdown(wait=False, cancel_futures=True)
                        break
                    result = fut.result()
                    done  += 1
                    if result["status"] == "Found":    found   += 1
                    elif result["status"] != "Skipped": not_fnd += 1

                    self._email_results.append(result)
                    pct = done / total
                    self.after(0, self._email_add_row, result, done, total, found, not_fnd, pct)

            self.after(0, self._email_done, found, not_fnd, done)

        threading.Thread(target=run, daemon=True).start()

    def _email_add_row(self, result, done, total, found, not_found, pct):
        status = result["status"]
        tag    = "found" if status == "Found" else ("error" if status == "Error" else "notfound")
        self._email_tree.insert("", "end", values=(
            result["domain"],
            result["email"]       or "—",
            result["email_valid"] or "",
            result["email_score"] or "",
            status,
        ), tags=(tag,))
        self._email_tree.yview_moveto(1)
        self._email_prog.set(pct)
        self._email_status.configure(text=f"Processing {done}/{total}")
        self._email_stats_lbl.configure(
            text=f"Found: {found}  |  Not Found: {not_found}  |  Done: {done}/{total}")

    def _email_done(self, found, not_found, total):
        self._email_start_btn.configure(state="normal")
        self._email_prog.set(1.0)
        self._email_status.configure(text=f"✅ Done — {found} emails found out of {total}")

    def _stop_bulk_email(self):
        self._email_stop = True
        self._email_status.configure(text="⏹ Stopped")
        self._email_start_btn.configure(state="normal")

    def _email_right_click(self, event):
        sel = self._email_tree.selection()
        if not sel: return
        row_idx = self._email_tree.index(sel[0])
        if row_idx >= len(self._email_results): return
        r = self._email_results[row_idx]
        menu = tk.Menu(self, tearoff=0, bg="#1A2540", fg="#E2E8F0",
                       activebackground="#243548", font=("Segoe UI",9))
        menu.add_command(label="📋 Copy Email",
                         command=lambda: self._copy_text(r.get("email","")))
        menu.add_command(label="🌐 Copy Domain",
                         command=lambda: self._copy_text(r.get("domain","")))
        menu.add_command(label="📋 Copy Row",
                         command=lambda: self._copy_text(
                             f"{r.get('domain','')},{r.get('email','')}"))
        menu.post(event.x_root, event.y_root)

    def _export_email_csv(self, found_only=False):
        if not self._email_results:
            messagebox.showinfo("Empty","Run finder first"); return
        if found_only:
            rows = [r for r in self._email_results if r.get("status") == "Found"]
            if not rows:
                messagebox.showwarning("No Results","No emails found to export"); return
            fname = "bulk_emails_found"
            label = f"{len(rows)} found emails"
        else:
            rows = self._email_results
            fname = "bulk_emails_all"
            label = f"{len(rows)} total rows"
        path = self._get_save_path("csv", fname)
        if not path: return
        with open(path,"w",newline="",encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["domain","email","email_valid",
                                               "email_score","status","website","error"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        messagebox.showinfo("✅ Saved", f"{label} exported → {path}")

    def _clear_email_results(self):
        self._email_results = []
        for item in self._email_tree.get_children():
            self._email_tree.delete(item)
        self._email_stats_lbl.configure(text="Found: 0  |  Not Found: 0  |  Total: 0")
        self._email_prog.set(0)
        self._email_status.configure(text="Cleared")

    # ══════════════════════════════════════════════════════════════
    #  HEATMAP ACTIONS
    # ══════════════════════════════════════════════════════════════
    def _hmap_log_msg(self, msg, tag="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._hmap_log.configure(state="normal")
        self._hmap_log.insert("end", f"[{ts}] ", "dim")
        self._hmap_log.insert("end", f"{msg}\n", tag)
        self._hmap_log.see("end")
        self._hmap_log.configure(state="disabled")

    def _start_heatmap(self):
        biz    = self._hmap_biz.get().strip()
        city   = self._hmap_city.get().strip()
        kw     = self._hmap_kw.get().strip()

        if not biz:   messagebox.showwarning("Missing","Enter business name"); return
        if not city:  messagebox.showwarning("Missing","Enter city/location"); return
        if not kw:    messagebox.showwarning("Missing","Enter keyword"); return

        method = self._hmap_method.get()
        if method == "Google Places API":
            api_key = self._settings.get("places_api_key","")
            if not api_key:
                messagebox.showerror("No API Key",
                    "Add Google Places API key in Settings tab first!")
                return
        else:
            api_key = ""

        grid_str  = self._hmap_grid.get()
        grid_size = int(grid_str[0])
        radius    = self._hmap_radius_val
        total_pts = grid_size * grid_size

        self._hmap_stop = False
        self._hmap_start_btn.configure(state="disabled")
        self._hmap_prog.set(0)
        self._hmap_status.configure(text=f"Geocoding {city}...")
        self._hmap_counter.configure(text=f"0 / {total_pts} points")

        # Clear log
        self._hmap_log.configure(state="normal")
        self._hmap_log.delete("1.0","end")
        self._hmap_log.configure(state="disabled")

        def run():
            self.after(0, self._hmap_log_msg,
                       f"🗺️ Heatmap: {biz} | {kw} | {city}", "info")

            # Step 1: Geocode
            self.after(0, self._hmap_log_msg, f"📍 Geocoding: {city}...", "info")
            lat, lng = geocode_location(city)
            if lat is None:
                self.after(0, self._hmap_log_msg,
                           f"❌ Could not geocode '{city}'", "error")
                self.after(0, self._hmap_start_btn.configure, {"state":"normal"})
                return

            self.after(0, self._hmap_log_msg,
                       f"✅ Location: {lat:.4f}, {lng:.4f}", "success")

            # Step 2: Build grid
            points = build_grid(lat, lng, radius, grid_size)
            self.after(0, self._hmap_log_msg,
                       f"📐 Grid: {grid_size}×{grid_size} = {len(points)} points "
                       f"| Radius: {radius}km", "info")

            # Step 3: Check rank at each point
            grid_results = []
            radius_m     = int(radius * 1000)

            for i, pt in enumerate(points):
                if self._hmap_stop:
                    self.after(0, self._hmap_log_msg, "⏹ Stopped.", "warn")
                    break

                self.after(0, self._hmap_log_msg,
                           f"  Checking point {i+1}/{len(points)} "
                           f"({pt['lat']:.4f}, {pt['lng']:.4f})...", "info")

                if method == "Google Places API" and api_key:
                    res = check_rank_places_api(
                        api_key, kw, biz, pt["lat"], pt["lng"], radius_m)
                else:
                    res = check_rank_selenium(
                        kw, biz, pt["lat"], pt["lng"], radius_m,
                        lambda m,t="info": self.after(0, self._hmap_log_msg, m, t))

                pt["rank"]       = res["rank"]
                pt["found"]      = res["found"]
                pt["businesses"] = res.get("businesses",[])
                grid_results.append(pt)

                rank_txt = (f"#{res['rank']}" if res["found"]
                            else f"Not found (>{res['rank']-1})")
                tag = "success" if res.get("found") and res["rank"] <= 3 else                       "warn"    if res.get("found") and res["rank"] <= 10 else "error"
                self.after(0, self._hmap_log_msg,
                           f"  Result: {rank_txt}", tag)

                # Update progress
                pct = (i+1) / len(points)
                self.after(0, self._hmap_prog.set, pct)
                self.after(0, self._hmap_counter.configure,
                           {"text": f"{i+1} / {len(points)} points"})
                self.after(0, self._hmap_status.configure,
                           {"text": f"Checking point {i+1}/{len(points)}..."})

                # Delay between points (selenium only)
                if method != "Google Places API":
                    time.sleep(random.uniform(2, 4))

            if self._hmap_stop:
                self.after(0, self._hmap_start_btn.configure, {"state":"normal"})
                return

            # Step 4: Generate map
            self.after(0, self._hmap_log_msg,
                       "🎨 Generating heatmap HTML...", "info")
            html_file = generate_heatmap_html(
                kw, biz, lat, lng, grid_results,
                grid_size, city)

            if html_file:
                self._hmap_file = html_file
                self.after(0, self._hmap_log_msg,
                           f"✅ Heatmap saved: {html_file}", "success")
                self.after(0, self._hmap_log_msg,
                           "🌐 Opening in browser...", "info")
                self.after(500, lambda: webbrowser.open(f"file://{html_file}"))
                self.after(0, self._hmap_status.configure,
                           {"text": "✅ Done! Heatmap opened in browser."})
            else:
                self.after(0, self._hmap_log_msg,
                           "❌ Failed to generate heatmap. Is folium installed?", "error")

            self.after(0, self._hmap_start_btn.configure, {"state":"normal"})
            self.after(0, self._hmap_prog.set, 1.0)

        threading.Thread(target=run, daemon=True).start()

    def _stop_heatmap(self):
        self._hmap_stop = True
        self._hmap_status.configure(text="⏹ Stopped")
        self._hmap_start_btn.configure(state="normal")

    def _open_heatmap(self):
        if self._hmap_file and os.path.exists(self._hmap_file):
            webbrowser.open(f"file://{self._hmap_file}")
        else:
            messagebox.showinfo("No Heatmap",
                "Generate a heatmap first, then click this.")

    def _clear(self):
        if self._running:
            messagebox.showwarning("Running","Stop scraper first"); return
        if not messagebox.askyesno("Clear?","Delete all results?"): return
        self._results = []
        for item in self._tree.get_children(): self._tree.delete(item)
        for v in [self._v_total,self._v_email,self._v_new,self._v_hot,self._v_phone]:
            v.set("0")
        self._prog_var.set(0)
        self._prog_lbl.configure(text="0 / 0")
        self._status_var.set("Cleared.")


# ═════════════════════════════════════════════════════════════════════
#  RUN
# ═════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    app = LeadHunterApp()
    app.mainloop()