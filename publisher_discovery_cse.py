import os
import csv
import time
import argparse
import requests
import re
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional, Tuple


# ============================================================
# Domain / URL helpers
# ============================================================

# Block these domains (and ALL subdomains)
BAD_DOMAIN_SUFFIXES = {
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "linkedin.com",
    "youtube.com", "maps.google.com", "google.com", "goo.gl",
    "yelp.com", "yellowpages.com", "mapquest.com", "tripadvisor.com", "wikipedia.org",
    "crunchbase.com", "bbb.org", "glassdoor.com", "indeed.com",
}

# Expanded TLD allowlist (still English/US-ish, but less restrictive)
ALLOWED_TLDS = {
    ".com", ".org", ".net", ".us", ".news", ".tv", ".fm", ".radio", ".io",
    ".co", ".biz", ".me",
}

# Optional: block certain TLDs
BAD_TLDS = {".gov"}


def clean_domain(url: str) -> str:
    """
    Extract a normalized domain from a URL or bare domain.
    """
    if not url:
        return ""
    u = url.strip()

    if "://" not in u:
        u = "https://" + u

    try:
        parsed = urlparse(u)
        host = (parsed.netloc or "").lower().strip()

        # Some weird URLs can land domain in path; attempt fallback
        if not host and parsed.path and "." in parsed.path and "/" not in parsed.path:
            host = parsed.path.lower().strip()

        # Remove credentials if present
        if "@" in host:
            host = host.split("@", 1)[-1]

        # Remove port
        if ":" in host:
            host = host.split(":", 1)[0]

        # Normalize common subdomains
        if host.startswith("www."):
            host = host[4:]
        if host.startswith("m."):
            host = host[2:]

        return host
    except Exception:
        return ""


def is_bad_domain_suffix(domain: str) -> bool:
    """
    Blocks exact domains and subdomains:
      - facebook.com
      - www.facebook.com
      - foo.bar.facebook.com
    """
    d = (domain or "").lower().strip()
    if not d:
        return True

    for bad in BAD_DOMAIN_SUFFIXES:
        if d == bad or d.endswith("." + bad):
            return True
    return False


def domain_tld_ok(domain: str) -> bool:
    if not domain or "." not in domain:
        return False

    if is_bad_domain_suffix(domain):
        return False

    for tld in BAD_TLDS:
        if domain.endswith(tld):
            return False

    return any(domain.endswith(tld) for tld in ALLOWED_TLDS)


def build_sitemap_xml(website_url_or_domain: str) -> str:
    d = clean_domain(website_url_or_domain)
    return f"https://{d}/sitemap.xml" if d else ""


# ============================================================
# City list
# ============================================================

def get_top_cities_by_state(state_code: str, n: int = 250, cities_file: str = "uscities.csv") -> List[str]:
    state_code = state_code.upper()
    cities: List[Tuple[str, int]] = []

    if not os.path.exists(cities_file):
        raise FileNotFoundError(
            f"Cities file not found: {cities_file}\n"
            f"Put SimpleMaps uscities.csv in the same folder, or pass --cities-file with a full path."
        )

    with open(cities_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                if (row.get("state_id") or "").upper() != state_code:
                    continue
                city = (row.get("city") or "").strip()
                pop_raw = row.get("population")
                if not city or pop_raw in (None, "", "0"):
                    continue
                pop = int(float(pop_raw))
                cities.append((city, pop))
            except Exception:
                continue

    cities.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in cities[:n]]


# ============================================================
# English-only HARD FILTER
# ============================================================

EN_SIGNAL_WORDS = {
    "the", "and", "for", "with", "from", "news", "daily", "times", "post", "press", "journal",
    "county", "city", "state", "police", "schools", "weather", "sports", "breaking", "local",
    "obituaries", "subscribe", "editorial", "opinion", "community", "report", "reporter",
    "public", "records", "election", "court", "crime", "business"
}

ES_MARKERS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "y", "o", "para", "por", "con",
    "noticias", "periódico", "periodico", "diario", "edición", "edicion",
    "español", "espanol", "última", "ultima", "hoy", "mañana", "manana",
    "gobierno", "policía", "policia"
}

WORD_RE = re.compile(r"[a-záéíóúñü]+")


def looks_english(text: str) -> bool:
    """
    Heuristic: reject if too many non-ascii or too many Spanish marker hits.
    Require at least 1 English signal word.
    """
    if not text:
        return False
    s = text.strip().lower()
    if not s:
        return False

    non_ascii = sum(1 for ch in s if ord(ch) > 127)
    if non_ascii / max(1, len(s)) > 0.08:
        return False

    tokens = WORD_RE.findall(s)
    if not tokens:
        return False

    en_hits = sum(1 for t in tokens if t in EN_SIGNAL_WORDS)
    es_hits = sum(1 for t in tokens if t in ES_MARKERS)

    if es_hits >= 2 and es_hits >= en_hits:
        return False

    return en_hits >= 1


# ============================================================
# Publisher-likeness filters + scoring
# ============================================================

TITLE_NEWS_KEYWORDS = {
    "news", "times", "tribune", "gazette", "herald", "post", "press",
    "journal", "observer", "telegraph", "daily", "chronicle", "sun",
    "sentinel", "record", "reporter", "bulletin", "advertiser", "star",
    "radio", "tv", "station", "media", "mirror", "banner"
}

BAD_NAME_KEYWORDS = {
    "walmart", "barnes", "costco", "target", "safeway", "kroger", "whole foods",
    "7-eleven", "7/11", "gamestop", "restaurant", "cafe", "coffee", "bar", "grill",
    "hotel", "resort", "bank", "credit union", "clinic", "hospital", "dentist",
    "urgent care", "school", "university", "college", "church", "temple", "mosque",
    "real estate", "realtor", "law firm", "attorney", "auto", "car dealership",
    "pharmacy", "department of", "city of", "county of", "state of",
    "chamber of commerce", "tourism", "visitor", "museum"
}

AGGREGATOR_SIGNALS = {
    "yelp", "yellowpages", "mapquest", "tripadvisor", "wikipedia",
    "crunchbase", "bbb.org", "glassdoor", "indeed", "opencorporates"
}

NAV_NEWSROOM_SIGNALS = {
    "subscribe", "subscriptions", "newsletter", "newsroom", "breaking", "latest",
    "obituaries", "sports", "weather", "local", "business", "politics", "opinion",
    "editorial", "classifieds", "advertise", "advertising", "media kit",
    "contact", "about", "staff", "reporters", "pressroom"
}

NON_NEWS_PAGE_SIGNALS = {
    "hours", "directions", "menu", "reservation", "reviews", "appointments",
    "tickets", "donate", "donation", "membership", "careers", "job openings"
}

TITLE_CLEAN_RE = re.compile(
    r"""
    \s*(
        \|\s*home.*$|
        \|\s*official\s*site.*$|
        -\s*official\s*site.*$|
        :\s*breaking\s*news.*$|
        \|\s*breaking\s*news.*$|
        \|\s*latest\s*news.*$|
        -\s*news.*$|
        \|\s*news.*$
    )
    """,
    re.IGNORECASE | re.VERBOSE
)


def normalize_publisher_name(title: str, domain: str) -> str:
    t = (title or "").strip()
    if not t:
        return t

    t = TITLE_CLEAN_RE.sub("", t).strip()

    if domain:
        d = domain.lower()
        if d in t.lower():
            t = re.sub(re.escape(d), "", t, flags=re.IGNORECASE).strip(" -|:")

    t = re.sub(r"\s+", " ", t).strip(" -|:")
    return t


def has_any_keyword(blob: str, kws: set) -> bool:
    b = (blob or "").lower()
    return any(k in b for k in kws)


def is_bad_name(title: str) -> bool:
    t = (title or "").lower()
    return any(b in t for b in BAD_NAME_KEYWORDS)


def is_aggregator(title: str, link: str, snippet: str) -> bool:
    blob = f"{(title or '').lower()} {(link or '').lower()} {(snippet or '').lower()}"
    return any(x in blob for x in AGGREGATOR_SIGNALS)


def is_bad_domain(domain: str) -> bool:
    if not domain:
        return True
    if is_bad_domain_suffix(domain):
        return True
    if not domain_tld_ok(domain):
        return True
    return False


def compute_confidence(title: str, snippet: str, domain: str) -> int:
    t = (title or "").lower()
    s = (snippet or "").lower()
    d = (domain or "").lower()
    blob = f"{t} {s} {d}"

    score = 0

    # Core news signals
    if has_any_keyword(t, TITLE_NEWS_KEYWORDS):
        score += 35
    if has_any_keyword(blob, NAV_NEWSROOM_SIGNALS):
        score += 35

    # Domain signal
    if any(k in d for k in ("news", "times", "daily", "press", "journal", "tribune", "gazette", "herald", "radio", "tv")):
        score += 12

    # English signal
    if looks_english(title):
        score += 10
    if looks_english(snippet):
        score += 5

    # Penalties
    if has_any_keyword(blob, NON_NEWS_PAGE_SIGNALS):
        score -= 20
    if is_bad_name(title):
        score -= 40
    if is_aggregator(title, f"https://{domain}/", snippet):
        score -= 50

    return max(0, min(100, score))


def looks_like_publisher(
    item: Dict[str, Any],
    min_confidence: int,
    debug_rejects: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    """
    Gate (less brittle, more recall):
    - Domain must be acceptable.
    - Not an aggregator.
    - English filter must pass on title OR snippet.
    - Must have at least one strong signal:
        title keyword OR nav snippet OR domain keyword
    - Confidence must be >= min_confidence.
    """
    title_raw = (item.get("title") or "").strip()
    link = (item.get("link") or "").strip()
    snippet = (item.get("snippet") or "").strip()

    if not title_raw or not link:
        return None

    domain = clean_domain(link)
    if is_bad_domain(domain):
        if debug_rejects is not None:
            debug_rejects.append(f"REJECT bad_domain={domain} link={link}")
        return None

    if is_aggregator(title_raw, link, snippet):
        if debug_rejects is not None:
            debug_rejects.append(f"REJECT aggregator domain={domain} title={title_raw[:80]}")
        return None

    title = normalize_publisher_name(title_raw, domain)
    if not title:
        if debug_rejects is not None:
            debug_rejects.append(f"REJECT empty_title domain={domain}")
        return None

    if is_bad_name(title):
        if debug_rejects is not None:
            debug_rejects.append(f"REJECT bad_name title={title[:80]}")
        return None

    if not looks_english(title) and not looks_english(snippet):
        if debug_rejects is not None:
            debug_rejects.append(f"REJECT non_english title={title[:70]}")
        return None

    title_has_news = has_any_keyword(title, TITLE_NEWS_KEYWORDS)
    snippet_has_nav = has_any_keyword(snippet, NAV_NEWSROOM_SIGNALS)
    domain_has_news = any(k in domain.lower() for k in (
        "news", "times", "press", "journal", "tribune", "gazette", "herald", "radio", "tv", "daily"
    ))

    if not (title_has_news or snippet_has_nav or domain_has_news):
        if debug_rejects is not None:
            debug_rejects.append(f"REJECT no_news_evidence domain={domain} title={title[:70]}")
        return None

    confidence = compute_confidence(title, snippet, domain)
    if confidence < min_confidence:
        if debug_rejects is not None:
            debug_rejects.append(f"REJECT low_conf={confidence}<{min_confidence} domain={domain} title={title[:70]}")
        return None

    return {
        "name": title,
        "website": f"https://{domain}/",
        "domain": domain,
        "sitemap_xml": build_sitemap_xml(domain),
        "confidence": str(confidence),
    }


# ============================================================
# Google Custom Search Client
# ============================================================

class CSEClient:
    def __init__(self, api_key: str, cx: str, timeout_s: int = 30, max_retries: int = 2):
        self.api_key = (api_key or "").strip()
        self.cx = (cx or "").strip()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.session = requests.Session()

        if not self.api_key:
            raise RuntimeError("Missing API key. Pass --api-key or set env var CSE_API_KEY.")
        if not self.cx:
            raise RuntimeError("Missing CX. Pass --cx or set env var CSE_CX.")

    def search(self, q: str, start: int = 1, num: int = 10) -> Dict[str, Any]:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.api_key,
            "cx": self.cx,
            "q": q,
            "num": num,
            "start": start,
            "hl": "en",
            "lr": "lang_en",
            "gl": "us",
            "filter": "1",
        }

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout_s)

                if r.status_code == 429:
                    raise RuntimeError(f"429 rate limit/quota hit: {r.text}")
                if r.status_code >= 400:
                    raise RuntimeError(f"{r.status_code} {r.reason}: {r.text}")

                return r.json()

            except Exception as e:
                last_err = e
                time.sleep(0.6 * (2 ** attempt))

        raise RuntimeError(f"Custom Search API failure after retries: {last_err}")


# ============================================================
# Discovery logic
# ============================================================

def build_query(city: str, state: str) -> str:
    city = city.strip()
    state = state.strip().upper()

    core = (
        f'"{city}" {state} '
        f'(newspaper OR news OR "daily" OR herald OR times OR tribune OR gazette OR journal '
        f'OR "radio" OR "tv station" OR "local news")'
    )

    excludes = (
        "-site:facebook.com -site:instagram.com -site:twitter.com -site:x.com "
        "-site:tiktok.com -site:linkedin.com -site:wikipedia.org -site:yelp.com "
        "-site:mapquest.com -site:yellowpages.com -site:tripadvisor.com "
        "-site:crunchbase.com -site:glassdoor.com -site:indeed.com "
        "-espanol -español -noticias -periódico -periodico -diario "
    )

    # Expanded bias to match expanded allowlist
    tld_bias = (
        "(site:.com OR site:.org OR site:.net OR site:.us OR site:.news OR "
        "site:.tv OR site:.fm OR site:.co OR site:.biz OR site:.me)"
    )

    return f"{core} {tld_bias} {excludes}"


def fetch_publishers_cse(
    api_key: str,
    cx: str,
    state_code: str,
    limit: int,
    cities_file: str = "uscities.csv",
    cities: int = 25,
    max_cities: int = 250,
    pages_per_query: int = 1,
    max_queries: int = 95,
    throttle_s: float = 1.0,
    min_confidence: int = 45,
    debug_rejects_n: int = 0,
    verbose: bool = True,
) -> Tuple[List[Dict[str, str]], int, List[str]]:

    target = max(1, min(int(limit), 200))
    state_code = state_code.upper()

    client = CSEClient(api_key=api_key, cx=cx)
    all_cities = get_top_cities_by_state(state_code, n=max_cities, cities_file=cities_file)
    city_list = all_cities[:max(1, int(cities))]

    results: List[Dict[str, str]] = []
    seen_domains = set()
    queries_used = 0
    rejects: List[str] = []

    for idx, city in enumerate(city_list, start=1):
        if len(results) >= target or queries_used >= max_queries:
            break

        q = build_query(city, state_code)

        if verbose:
            print(f"[{state_code}] city {idx}/{len(city_list)}: {city} | found={len(results)}/{target} | queries={queries_used}/{max_queries}")

        for page in range(max(1, int(pages_per_query))):
            if len(results) >= target or queries_used >= max_queries:
                break

            start = 1 + page * 10
            queries_used += 1

            try:
                data = client.search(q=q, start=start, num=10)
            except Exception as e:
                msg = str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "Quota exceeded" in msg:
                    print(f"STOP: rate limit/quota hit at queries_used={queries_used}. Error={msg[:220]}...")
                    return results, queries_used, rejects
                if verbose:
                    print(f"  [CSE] {type(e).__name__}: {e}")
                break

            for it in (data.get("items") or []):
                debug_bucket = rejects if debug_rejects_n > 0 and len(rejects) < debug_rejects_n else None

                row = looks_like_publisher(
                    it,
                    min_confidence=min_confidence,
                    debug_rejects=debug_bucket,
                )
                if not row:
                    continue

                d = row["domain"]
                if d in seen_domains:
                    continue
                seen_domains.add(d)

                results.append({
                    "state": state_code,
                    "city_seed": city,
                    "name": row["name"],
                    "website": row["website"],
                    "domain": row["domain"],
                    "sitemap_xml": row["sitemap_xml"],
                    "confidence": row["confidence"],
                })

                if len(results) >= target:
                    break

            time.sleep(max(0.0, float(throttle_s)))

    results.sort(key=lambda r: int(r.get("confidence", "0")), reverse=True)
    return results, queries_used, rejects


def save_csv(rows: List[Dict[str, str]], out_path: str) -> str:
    fieldnames = ["state", "city_seed", "name", "website", "domain", "sitemap_xml", "confidence"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Publisher discovery via Google Custom Search (English-only, query-capped, confidence scored)."
    )

    parser.add_argument("--state", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--cities-file", default="uscities.csv")
    parser.add_argument("--cities", type=int, default=25)
    parser.add_argument("--max-cities", type=int, default=250)
    parser.add_argument("--pages-per-query", type=int, default=1)
    parser.add_argument("--max-queries", type=int, default=95)
    parser.add_argument("--throttle", type=float, default=1.0, help="Seconds to sleep between API calls (avoid 429).")
    parser.add_argument("--min-confidence", type=int, default=45, help="Keep results with confidence >= this (default 45).")
    parser.add_argument("--debug-rejects", type=int, default=0, help="Print up to N rejection reasons (helps tuning).")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--cx", default=None)

    args = parser.parse_args()

    api_key = (args.api_key or os.getenv("CSE_API_KEY") or "").strip()
    cx = (args.cx or os.getenv("CSE_CX") or "").strip()

    rows, used, rejects = fetch_publishers_cse(
        api_key=api_key,
        cx=cx,
        state_code=args.state,
        limit=args.limit,
        cities_file=args.cities_file,
        cities=args.cities,
        max_cities=args.max_cities,
        pages_per_query=args.pages_per_query,
        max_queries=args.max_queries,
        throttle_s=args.throttle,
        min_confidence=args.min_confidence,
        debug_rejects_n=args.debug_rejects,
        verbose=(not args.quiet),
    )

    out = f"publishers_CSE_{args.state.upper()}_{args.limit}.csv"
    save_csv(rows, out)

    print(f"Saved {len(rows)} publishers to {out} | queries_used={used}/{args.max_queries}")

    if args.debug_rejects and rejects:
        print("\n--- DEBUG REJECTS (sample) ---")
        for line in rejects[: args.debug_rejects]:
            print(line)


if __name__ == "__main__":
    main()
