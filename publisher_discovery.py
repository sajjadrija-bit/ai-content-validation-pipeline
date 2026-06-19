import os
import csv
import time
import json
import argparse
import requests
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Dict, Any, List, Tuple

# ============================================================
# CONFIGURATION
# ============================================================

HARD_BLOCKED = {
    # Social media
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com",
    "linkedin.com", "youtube.com", "pinterest.com", "reddit.com",
    # Google
    "google.com", "goo.gl", "googleapis.com",
    # Aggregators/directories
    "yelp.com", "yellowpages.com", "tripadvisor.com", "wikipedia.org",
    "patch.com", "newsbreak.com", "ground.news", "usnews.com",
    # National news
    "nytimes.com", "washingtonpost.com", "usatoday.com", "cnn.com",
    "foxnews.com", "nbcnews.com", "cbsnews.com", "npr.org", "apnews.com",
    # Weather/time/tides
    "weather.com", "accuweather.com", "weather.gov", "noaa.gov",
    "almanac.com", "timeanddate.com", "tidetime.org", "willyweather.com",
    # Commerce
    "amazon.com", "walmart.com", "target.com", "zillow.com", "realtor.com",
    # Obituaries
    "legacy.com", "tributes.com",
    # Transit
    "amtrak.com", "amtrakdowneaster.com", "concordcoachlines.com",
    # Archives (not active news)
    "digitalmaine.com", "loc.gov", "archive.org",
    # Religious
    "islamicfinder.org", "discovermass.com",
    # Trade/PR publications
    "agilitypr.com", "supplyht.com",
    # Out of state news
    "vineyardgazette.com", "noozhawk.com", "lmtribune.com", "news-herald.com",
    # Misc non-news
    "solvhealth.com", "hannaford.com", "passportsandvisas.com", "trolleytours.com",
}

BLOCKED_SUBDOMAINS = {"jobs.", "careers.", "obituaries.", "locations.", "stores.", "ftp.", "events.", "calendar."}

# Block these domain keywords
BLOCKED_KEYWORDS = {
    "golf", "country-club", "countryclub", "golf-club", "golfclub",
    "dental", "dentist", "dentistry",
    "veterinary", "vet.", "animal-hospital",
    "hotel", "inn", "resort", "motel",
    "brewery", "brewing", "winery", "distillery",
    "church", "methodist", "baptist", "catholic", "episcopal", "trinity",
    "library", "libraries",
    "museum",
    "ymca", "ywca",
    "transit", "bus", "metro", "transportation",
    "housing", "apartments",
    "medical", "hospital", "health", "clinic", "urgent-care",
    "school", "schools", "k12", "rsu",
    "realty", "realtor", "real-estate",
    "salon", "spa", "barber",
    "theater", "theatre", "cinema", "strand", "colonial",
    "festival", "balloon",
    "ski", "skiing", "snowboard",
    "waterpark", "amusement",
    "funeral", "mortuary", "memorial",
    "goodwill", "salvation-army",
    "passport", "visa",
    "dna", "testing",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============================================================
# UTILITIES
# ============================================================

def clean_domain(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        p = urlparse(url)
        h = (p.netloc or "").lower()
        if ":" in h:
            h = h.split(":")[0]
        if h.startswith("www."):
            h = h[4:]
        return h
    except:
        return ""

def is_hard_blocked(domain: str) -> bool:
    d = domain.lower()
    # Check exact domain and suffix matches
    for b in HARD_BLOCKED:
        if d == b or d.endswith("." + b):
            return True
    # Check subdomain patterns
    for s in BLOCKED_SUBDOMAINS:
        if d.startswith(s):
            return True
    # Check keyword patterns in domain
    for kw in BLOCKED_KEYWORDS:
        if kw in d.replace("-", ""):
            return True
    return False

# ============================================================
# HOMEPAGE ANALYSIS
# ============================================================

def analyze_homepage(domain: str, timeout: int = 8) -> Dict[str, Any]:
    result = {"domain": domain, "fetched": False, "signals": {}, "score": 0, "evidence": []}
    try:
        url = f"https://{domain}/"
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA}, allow_redirects=True)
        if r.status_code != 200:
            result["error"] = f"http_{r.status_code}"
            return result

        result["fetched"] = True
        html = r.text.lower()
        html_orig = r.text

        # === POSITIVE SIGNALS ===

        # RSS feed
        if any(re.search(p, html) for p in [r'application/rss\+xml', r'/feed', r'/rss', r'\.rss']):
            result["signals"]["has_rss"] = True
            result["score"] += 25
            result["evidence"].append("RSS feed")

        # NewsArticle schema
        if '"newsarticle"' in html or '"article"' in html or '@type": "news' in html:
            result["signals"]["has_news_schema"] = True
            result["score"] += 30
            result["evidence"].append("News schema")

        # Article tags
        article_count = html.count("<article")
        if article_count >= 3:
            result["signals"]["has_articles"] = article_count
            result["score"] += 20
            result["evidence"].append(f"{article_count} articles")

        # Bylines
        if re.search(r'by\s+[A-Z][a-z]+\s+[A-Z]', html_orig) or 'byline' in html or 'author' in html:
            result["signals"]["has_bylines"] = True
            result["score"] += 15
            result["evidence"].append("Bylines")

        # News sections
        news_sections = ["local", "politics", "sports", "opinion", "obituaries", "business", "crime", "breaking"]
        section_hits = sum(1 for s in news_sections if s in html[:5000])
        if section_hits >= 3:
            result["signals"]["has_news_sections"] = section_hits
            result["score"] += 20
            result["evidence"].append(f"{section_hits} news sections")

        # Subscribe/newsletter
        if 'subscribe' in html or 'newsletter' in html:
            result["signals"]["has_subscribe"] = True
            result["score"] += 10
            result["evidence"].append("Subscribe option")

        # Recent dates
        yr = datetime.now().year
        if re.search(rf'/{yr}/\d{{2}}/' , html) or re.search(rf'{yr}-\d{{2}}-\d{{2}}', html):
            result["signals"]["has_recent_dates"] = True
            result["score"] += 15
            result["evidence"].append("Recent dates")

        # === NEGATIVE SIGNALS ===

        # Restaurant
        rest_signals = ["menu", "reservations", "order online", "dine-in", "takeout", "happy hour"]
        if sum(1 for s in rest_signals if s in html) >= 2:
            result["signals"]["is_restaurant"] = True
            result["score"] -= 50
            result["evidence"].append("Restaurant signals")

        # E-commerce
        ecom_signals = ["add to cart", "shop now", "buy now", "checkout", "shopping cart"]
        if sum(1 for s in ecom_signals if s in html) >= 2:
            result["signals"]["is_ecommerce"] = True
            result["score"] -= 40
            result["evidence"].append("E-commerce signals")

        # Government/municipal
        gov_signals = ["city hall", "town hall", "city council", "permits", "municipal", "public works", "mayor"]
        if sum(1 for s in gov_signals if s in html) >= 2:
            result["signals"]["is_government"] = True
            result["score"] -= 50
            result["evidence"].append("Government signals")

        # School
        school_signals = ["student", "faculty", "curriculum", "enrollment", "classroom", "principal"]
        if sum(1 for s in school_signals if s in html) >= 2:
            result["signals"]["is_school"] = True
            result["score"] -= 50
            result["evidence"].append("School signals")

        # Church
        church_signals = ["worship", "sermon", "pastor", "ministry", "sunday service", "bible"]
        if sum(1 for s in church_signals if s in html) >= 2:
            result["signals"]["is_church"] = True
            result["score"] -= 50
            result["evidence"].append("Church signals")

        # Music radio
        radio_signals = ["now playing", "listen live", "playlist", "request a song", "top 40"]
        if sum(1 for s in radio_signals if s in html) >= 2:
            result["signals"]["is_music_radio"] = True
            result["score"] -= 40
            result["evidence"].append("Music radio signals")

        # Service business
        svc_signals = ["our services", "book appointment", "get a quote", "free estimate"]
        if sum(1 for s in svc_signals if s in html) >= 2 and result["score"] < 30:
            result["signals"]["is_service"] = True
            result["score"] -= 30
            result["evidence"].append("Service business signals")

    except requests.Timeout:
        result["error"] = "timeout"
    except:
        result["error"] = "request_error"

    return result

# ============================================================
# LLM VALIDATION (Claude Opus)
# ============================================================

def validate_with_llm(candidates: List[Dict], state: str, api_key: str, model: str = "claude-opus-4-20250514", batch_size: int = 10) -> List[Dict]:
    if not api_key:
        print("  [LLM] No API key, skipping validation")
        return candidates

    validated = []

    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i+batch_size]

        candidate_list = "\n".join([
            f"- {c['domain']}: {c.get('name', 'Unknown')} (homepage_score: {c.get('homepage_score', 'N/A')}, evidence: {c.get('evidence', [])})"
            for c in batch
        ])

        prompt = f"""You are evaluating potential local news publishers for {state}.

CRITERIA FOR "PASS" (all must be true):
1. Local or state news publisher (newspaper, TV station, radio news, online news)
2. Publishes ORIGINAL local news (not syndicated/aggregated)
3. Covers local community: politics, crime, sports, obituaries, etc.
4. Located in or specifically covers {state}

CRITERIA FOR "FAIL" (any = fail):
- National outlets (NYT, CNN, Fox News, etc.)
- Aggregators (Patch, NewsBreak, etc.)
- Restaurants, hotels, shops, businesses
- Schools, churches, government/municipal sites
- Music radio stations (not news radio)
- Blogs, personal sites, portfolios
- Out-of-state publications
- Corporate PR newsrooms
- Weather/tides/almanac sites
- Obituary-only sites
- Job boards

CANDIDATES:
{candidate_list}

Respond ONLY with a JSON array (no other text):
[
  {{"domain": "example.com", "verdict": "PASS", "reason": "Local newspaper covering X county", "confidence": 95}},
  {{"domain": "example2.com", "verdict": "FAIL", "reason": "Restaurant website", "confidence": 99}}
]"""

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
                timeout=90,
            )

            if response.status_code != 200:
                print(f"  [LLM] API error: {response.status_code} - {response.text[:200]}")
                validated.extend(batch)
                continue

            data = response.json()
            content = data.get("content", [{}])[0].get("text", "")

            try:
                match = re.search(r'\[.*\]', content, re.S)
                if match:
                    results = json.loads(match.group(0))
                    result_map = {r["domain"]: r for r in results}
                    for c in batch:
                        if c["domain"] in result_map:
                            r = result_map[c["domain"]]
                            c["llm_verdict"] = r.get("verdict", "REVIEW")
                            c["llm_reason"] = r.get("reason", "")
                            c["llm_confidence"] = r.get("confidence", 50)
                        validated.append(c)
                else:
                    validated.extend(batch)
            except json.JSONDecodeError:
                print(f"  [LLM] JSON parse error")
                validated.extend(batch)

        except Exception as e:
            print(f"  [LLM] Error: {e}")
            validated.extend(batch)

        time.sleep(1)

    return validated

def discover_publishers_llm(state: str, cities: List[str], api_key: str, existing_domains: set, model: str = "claude-opus-4-20250514") -> List[Dict]:
    if not api_key:
        return []

    discovered = []

    for i in range(0, len(cities), 5):
        city_batch = cities[i:i+5]
        city_list = ", ".join(city_batch)

        prompt = f"""List ALL local news publishers (newspapers, TV stations, news radio, online news) for these {state} cities:

Cities: {city_list}

Include:
- Daily/weekly newspapers
- Local TV news stations (ABC/NBC/CBS/Fox affiliates)
- News radio stations (not music radio)
- Online-only local news sites
- Public radio/TV with news

Exclude:
- National outlets
- Music radio stations
- Blogs
- Government sites

Respond ONLY with JSON array:
[
  {{"domain": "example.com", "name": "Example Daily News", "type": "newspaper", "coverage": "Example City"}}
]"""

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
                timeout=90,
            )

            if response.status_code == 200:
                data = response.json()
                content = data.get("content", [{}])[0].get("text", "")
                match = re.search(r'\[.*\]', content, re.S)
                if match:
                    results = json.loads(match.group(0))
                    for r in results:
                        domain = clean_domain(r.get("domain", ""))
                        if domain and domain not in existing_domains and not is_hard_blocked(domain):
                            discovered.append({
                                "domain": domain,
                                "name": r.get("name", ""),
                                "type": r.get("type", ""),
                                "coverage": r.get("coverage", ""),
                                "source": "llm_discovery",
                            })
                            existing_domains.add(domain)
        except Exception as e:
            print(f"  [LLM Discovery] Error: {e}")

        time.sleep(1)

    return discovered

# ============================================================
# ACTIVITY CHECK
# ============================================================

SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/news-sitemap.xml", "/post-sitemap.xml", "/wp-sitemap.xml", "/rss", "/feed"]

def check_activity(domain: str, max_days: int = 60) -> Tuple[str, str]:
    cutoff = datetime.now() - timedelta(days=max_days)

    for path in SITEMAP_PATHS:
        try:
            r = requests.get(f"https://{domain}{path}", timeout=6, headers={"User-Agent": UA})
            if r.status_code != 200:
                continue

            dates = []
            for m in re.findall(r'(\d{4})-(\d{2})-(\d{2})', r.text):
                try:
                    dt = datetime(int(m[0]), int(m[1]), int(m[2]))
                    if dt <= datetime.now():
                        dates.append(dt)
                except:
                    pass

            for m in re.findall(r'/(\d{4})/(\d{2})/', r.text):
                try:
                    dt = datetime(int(m[0]), int(m[1]), 15)
                    if dt <= datetime.now():
                        dates.append(dt)
                except:
                    pass

            if dates:
                latest = max(dates)
                return ("active" if latest >= cutoff else "inactive", latest.strftime("%Y-%m-%d"))
        except:
            continue

    return "unknown", "no_dates"

# ============================================================
# CSE CLIENT
# ============================================================

class CSEClient:
    def __init__(self, api_key: str, cx: str):
        self.api_key = api_key
        self.cx = cx
        self.session = requests.Session()

    def search(self, q: str, start: int = 1) -> Dict:
        r = self.session.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": self.api_key, "cx": self.cx, "q": q, "num": 10, "start": start, "hl": "en", "lr": "lang_en", "gl": "us"},
            timeout=30,
        )
        if r.status_code == 429:
            raise RuntimeError("CSE quota exceeded")
        if r.status_code >= 400:
            raise RuntimeError(f"CSE error {r.status_code}")
        return r.json()

# ============================================================
# CITY LIST
# ============================================================

def get_cities(state: str, n: int, path: str) -> List[str]:
    cities = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("state_id") or "").upper() == state.upper():
                    city = (row.get("city") or "").strip()
                    pop = float(row.get("population") or 0)
                    if city and pop > 0:
                        cities.append((city, pop))
    except FileNotFoundError:
        print(f"Warning: Cities file not found: {path}")
        return []
    cities.sort(key=lambda x: -x[1])
    return [c for c, _ in cities[:n]]

def build_query(city: str, state: str) -> str:
    return (
        f'"{city}" {state} (newspaper OR "local news" OR "tv news" OR '
        f'herald OR times OR tribune OR gazette OR journal OR "public radio") '
        f'-site:facebook.com -site:twitter.com -site:wikipedia.org '
        f'-site:yelp.com -site:patch.com -site:newsbreak.com '
    )

# ============================================================
# SINGLE-STATE PIPELINE (unchanged logic)
# ============================================================

def run_one_state(state: str, args, cse_key: str, cse_cx: str, anthropic_key: str) -> Tuple[List[Dict], int, str]:
    """
    Runs the exact same logic as your original single-state script.
    Returns (results, queries_used, out_csv_path)
    """
    state = state.upper()
    verbose = not args.quiet

    client = CSEClient(cse_key, cse_cx)
    cities = get_cities(state, args.cities_max, args.cities_file)[:args.cities_start]

    if verbose:
        print(f"{'='*60}")
        print(f"Publisher Discovery v5 - {state}")
        print(f"{'='*60}")
        print(f"Cities: {len(cities)} | LLM: {'enabled' if anthropic_key and not args.skip_llm else 'disabled'}")

    # PHASE 1: CSE Discovery
    if verbose:
        print(f"\n[Phase 1] CSE Discovery...")

    candidates = []
    seen = set()
    queries = 0

    for i, city in enumerate(cities, 1):
        if queries >= args.max_queries:
            break
        if verbose:
            print(f"  {i}/{len(cities)}: {city} | found={len(candidates)}")

        for page in range(args.pages_per_query):
            if queries >= args.max_queries:
                break
            queries += 1

            try:
                data = client.search(build_query(city, state), start=1 + page * 10)
                for item in data.get("items") or []:
                    domain = clean_domain(item.get("link", ""))
                    if not domain or domain in seen or is_hard_blocked(domain):
                        continue
                    seen.add(domain)
                    candidates.append({
                        "domain": domain,
                        "name": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                        "city_seed": city,
                        "source": "cse",
                    })
            except Exception as e:
                if verbose:
                    print(f"    Error: {e}")
                break

            time.sleep(args.throttle)

    if verbose:
        print(f"  Found {len(candidates)} candidates")

    # PHASE 2: LLM Discovery
    if anthropic_key and not args.skip_llm_discovery:
        if verbose:
            print(f"\n[Phase 2] LLM Discovery...")
        discovered = discover_publishers_llm(state, cities, anthropic_key, seen, args.llm_model)
        candidates.extend(discovered)
        if verbose:
            print(f"  Discovered {len(discovered)} additional publishers")

    # PHASE 3: Homepage Analysis
    if verbose:
        print(f"\n[Phase 3] Homepage Analysis ({len(candidates)} candidates)...")

    for i, c in enumerate(candidates):
        if verbose and (i + 1) % 10 == 0:
            print(f"  Analyzed {i+1}/{len(candidates)}")
        analysis = analyze_homepage(c["domain"])
        c["homepage_score"] = analysis["score"]
        c["evidence"] = analysis.get("evidence", [])
        c["signals"] = analysis.get("signals", {})
        c["fetch_error"] = analysis.get("error", "")
        time.sleep(0.1)

    # PHASE 4: LLM Validation
    if anthropic_key and not args.skip_llm:
        if verbose:
            print(f"\n[Phase 4] LLM Validation...")
        candidates = validate_with_llm(candidates, state, anthropic_key, args.llm_model)
        if verbose:
            print(f"  Validated {len(candidates)} candidates")

    # PHASE 5: Activity Check
    if args.require_sitemap_fresh:
        if verbose:
            print(f"\n[Phase 5] Activity Check...")
        for i, c in enumerate(candidates):
            if verbose and (i + 1) % 10 == 0:
                print(f"  Checked {i+1}/{len(candidates)}")
            status, info = check_activity(c["domain"], args.active_days)
            c["activity_status"] = status
            c["activity_date"] = info
            time.sleep(0.1)

    # FINAL VERDICT
    results = []
    for c in candidates:
        llm_verdict = c.get("llm_verdict", "").upper()
        homepage_score = c.get("homepage_score", 0)
        activity = c.get("activity_status", "unknown")

        if llm_verdict == "PASS":
            verdict = "PASS"
        elif llm_verdict == "FAIL":
            verdict = "DROP"
        elif homepage_score >= 50:
            verdict = "PASS" if activity != "inactive" else "REVIEW"
        elif homepage_score >= 20:
            verdict = "REVIEW"
        elif homepage_score <= -20:
            verdict = "DROP"
        else:
            verdict = "REVIEW"

        if activity == "inactive" and verdict == "PASS":
            verdict = "REVIEW"

        c["verdict"] = verdict
        results.append(c)

    # Sort
    order = {"PASS": 0, "REVIEW": 1, "DROP": 2}
    results.sort(key=lambda x: (order.get(x["verdict"], 9), -x.get("homepage_score", 0)))
    results = results[:args.limit]

    # Save
    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, f"publishers_{state}_{args.limit}.csv")

    fields = ["verdict", "domain", "name", "homepage_score", "llm_verdict", "llm_reason",
              "activity_status", "activity_date", "evidence", "city_seed", "source"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            r["evidence"] = "; ".join(r.get("evidence", []))
            w.writerow(r)

    # Summary
    pass_ct = sum(1 for r in results if r["verdict"] == "PASS")
    review_ct = sum(1 for r in results if r["verdict"] == "REVIEW")
    drop_ct = sum(1 for r in results if r["verdict"] == "DROP")

    print(f"\n{'='*60}")
    print(f"Results: {out_path}")
    print(f"{'='*60}")
    print(f"  ✅ PASS:   {pass_ct}")
    print(f"  ⚠️  REVIEW: {review_ct}")
    print(f"  ❌ DROP:   {drop_ct}")
    print(f"  Total:    {len(results)}")
    print(f"  Queries:  {queries}")

    return results, queries, out_path

# ============================================================
# MAIN (multi-state wrapper ONLY)
# ============================================================

def main():
    p = argparse.ArgumentParser(description="Publisher Discovery v5 (LLM-Validated) - Multi-State Wrapper")
    # NOTE: --state is no longer required so --states can be used
    p.add_argument("--state", default=None, help='Two-letter state (e.g., ME) or "ALL"')
    p.add_argument("--states", default=None, help="Comma-separated states (e.g., ME,NH,VT)")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--cities-file", default="uscities.csv")
    p.add_argument("--cities-start", type=int, default=25)
    p.add_argument("--cities-max", type=int, default=250)
    p.add_argument("--pages-per-query", type=int, default=1)
    p.add_argument("--max-queries", type=int, default=95)
    p.add_argument("--throttle", type=float, default=1.0)
    p.add_argument("--require-sitemap-fresh", action="store_true")
    p.add_argument("--active-days", type=int, default=60)
    p.add_argument("--outdir", default=".")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--api-key", default=None, help="Google CSE API key")
    p.add_argument("--cx", default=None, help="Google CSE CX")
    p.add_argument("--anthropic-key", default=None, help="Anthropic API key")
    p.add_argument("--llm-model", default="claude-opus-4-20250514")
    p.add_argument("--skip-llm", action="store_true", help="Skip LLM validation")
    p.add_argument("--skip-llm-discovery", action="store_true", help="Skip LLM discovery")

    args = p.parse_args()

    ALL_STATES = ["HI","ME","ND","DC","DE","NV","NH","AK","UT","SD","CT","NM","RI","VT","WY","ID","WV","KS","MD","AR","LA","SC","NJ","OK","MS","MT","MN","AL","NE","MO","CO","IA","AZ","MA","WI","OR","KY","MI","TN","IN","IL","FL","WA","VA","PA","OH","GA","NC","TX","NY","CA"]

    if args.states:
        states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    elif args.state and args.state.upper() == "ALL":
        states = ALL_STATES
    elif args.state:
        states = [args.state.upper()]
    else:
        print("ERROR: Provide --state XX, --state ALL, or --states XX,YY,...")
        return

    cse_key = args.api_key or os.getenv("CSE_API_KEY") or ""
    cse_cx = args.cx or os.getenv("CSE_CX") or ""
    anthropic_key = args.anthropic_key or os.getenv("ANTHROPIC_API_KEY") or ""

    if not cse_key or not cse_cx:
        print("ERROR: Missing CSE_API_KEY or CSE_CX")
        return

    # Run each state using the unchanged pipeline
    all_rows: List[Dict] = []
    summary_rows: List[Dict] = []

    for idx, st in enumerate(states, 1):
        print(f"\n{'='*60}")
        print(f"[{idx}/{len(states)}] Processing {st}")
        print(f"{'='*60}")

        try:
            results, queries_used, out_csv = run_one_state(st, args, cse_key, cse_cx, anthropic_key)
            # Track summary (based on final trimmed results, same as single-state)
            pc = sum(1 for r in results if r["verdict"] == "PASS")
            rc = sum(1 for r in results if r["verdict"] == "REVIEW")
            dc = sum(1 for r in results if r["verdict"] == "DROP")
            summary_rows.append({"state": st, "pass": pc, "review": rc, "drop": dc, "queries": queries_used, "out_csv": out_csv})
            # For compiled output, attach state (non-invasive)
            for r in results:
                rr = dict(r)
                rr["state"] = st
                all_rows.append(rr)
        except Exception as e:
            print(f"[{st}] ERROR: {e}")
            summary_rows.append({"state": st, "pass": 0, "review": 0, "drop": 0, "queries": 0, "out_csv": "", "error": str(e)})

    # Optional compiled outputs (only when multi-state)
    if len(states) > 1:
        comp_path = os.path.join(args.outdir, f"publishers_ALL_{len(states)}_states_compiled.csv")
        summ_path = os.path.join(args.outdir, "publishers_SUMMARY.csv")

        comp_fields = ["state","verdict","domain","name","homepage_score","llm_verdict","llm_reason",
                       "activity_status","activity_date","evidence","city_seed","source"]

        with open(comp_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=comp_fields, extrasaction="ignore")
            w.writeheader()
            for r in all_rows:
                r2 = dict(r)
                # ensure evidence is string
                if isinstance(r2.get("evidence"), list):
                    r2["evidence"] = "; ".join(r2["evidence"])
                w.writerow(r2)

        with open(summ_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["state","pass","review","drop","queries","out_csv","error"], extrasaction="ignore")
            w.writeheader()
            w.writerows(summary_rows)

        tp = sum(s.get("pass", 0) for s in summary_rows)
        tr = sum(s.get("review", 0) for s in summary_rows)
        td = sum(s.get("drop", 0) for s in summary_rows)

        print(f"\n{'='*60}")
        print(f"COMPLETE: {len(states)} states")
        print(f"{'='*60}")
        print(f"Compiled: {comp_path}")
        print(f"Summary:  {summ_path}")
        print(f"TOTALS: ✅ {tp} PASS | ⚠️ {tr} REVIEW | ❌ {td} DROP")

if __name__ == "__main__":
    main()
