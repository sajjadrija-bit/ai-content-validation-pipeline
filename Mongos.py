from pymongo import MongoClient
from datetime import datetime, timedelta
from collections import defaultdict
from urllib.parse import urlparse
import textwrap, re, hashlib

# ============================================================
# EDIT THESE
# ============================================================

MONGO_URI = "Eter Mongo Url"
DB_NAME = "sEnter DB name"
COLLECTION_NAME = "article_sot"

CENTER_DATE = "2026-01-20"   # <-- set this to the Daily Brief date you’re validating
WINDOW_DAYS = 10            

TITLES_FROM_LLLM = [
    "strengthen congress", 
    # paste more suspected titles here (from the PDF LLM step)
]

# Output controls
MAX_RESULTS_PER_TITLE = 8
BODY_PREVIEW_CHARS = 20000
LINE_WIDTH = 100

# Duplicate rules
SIMILARITY_THRESHOLD = 0.975   # very strict; keeps false positives low
ALLOW_ALL_PAYWALLED_IF_3PLUS = True

# Paywall heuristics (extra label, DOES NOT override system paywall flag)
MIN_READABLE_CHARS = 600
GIBBERISH_MAX_READABLE_RATIO = 0.70

# ============================================================
# Helpers
# ============================================================

def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")

def date_window(center_date: str, window_days: int):
    c = parse_date(center_date)
    start = (c - timedelta(days=window_days)).strftime("%Y-%m-%d")
    end = (c + timedelta(days=window_days)).strftime("%Y-%m-%d")
    return start, end

def domain_from_url(u: str) -> str:
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return "N/A"

def normalize_body(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip().lower()

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

def readable_ratio(text: str) -> float:
    if not text:
        return 0.0
    normal = sum(1 for ch in text if (
        ch.isalnum() or ch.isspace() or ch in ".,;:!?\"'’()[]{}-/—–"
    ))
    return normal / max(1, len(text))

def looks_paywalled_gibberish(raw_body: str) -> bool:
    if not raw_body:
        return True
    rr = readable_ratio(raw_body)
    has_marker = "k9bm" in raw_body.lower()  # seen in your example
    too_short = len(raw_body.strip()) < MIN_READABLE_CHARS
    return (rr < GIBBERISH_MAX_READABLE_RATIO) or has_marker or too_short

def strip_guest_prefix(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"^\s*(guest\s+column|opinion|editorial|letter)\s*:\s*", "", t, flags=re.IGNORECASE)
    return t.strip()

def tokens_from_title(title: str):
    # makes "Let’s" match "Lets"
    t = strip_guest_prefix(title).replace("’", "'").lower()
    t = re.sub(r"[’']", "", t)
    raw = re.findall(r"[a-z0-9]+", t)
    return [w for w in raw if len(w) >= 3]

def must_contain_all_tokens_regex(tokens):
    if not tokens:
        return r"(?!)"
    lookaheads = "".join([rf"(?=.*\b{re.escape(tok)}\b)" for tok in tokens])
    return lookaheads + r".*"

def similarity(a: str, b: str) -> float:
    # cheap, strict proxy: compare hashes first
    return 1.0 if a and b and a == b else 0.0

# ============================================================
# Connect
# ============================================================

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=15000)
client.admin.command("ping")
db = client[DB_NAME]
article_sot = db[COLLECTION_NAME]
print(f" Connected: {DB_NAME}.{COLLECTION_NAME}")

start, end = date_window(CENTER_DATE, WINDOW_DAYS)
print(f"Date window: {start} .. {end}\n")

projection = {
    "_id": 0,
    "article_id": 1,
    "is_paywalled": 1,
    "title": 1,
    "normalized_title": 1,
    "published_date": 1,
    "url": 1,
    "description": 1,
    "author": 1,
    "publisher": 1,
    "body": 1,
}

# ============================================================
# 1) Title-based pull
# ============================================================

all_docs = []
print("=== TITLE SEARCH RESULTS ===")
for t in TITLES_FROM_LLLM:
    toks = tokens_from_title(t)
    pat = must_contain_all_tokens_regex(toks)

    query = {
        "published_date": {"$gte": start, "$lte": end},
        "$or": [
            {"normalized_title": {"$regex": pat, "$options": "i"}},
            {"title": {"$regex": pat, "$options": "i"}},
            {"description": {"$regex": pat, "$options": "i"}},
        ]
    }

    count = article_sot.count_documents(query)
    print(f"\nSearching for: {t!r} | Matches: {count}")

    if count == 0:
        continue

    cursor = article_sot.find(query, projection=projection).limit(MAX_RESULTS_PER_TITLE)
    docs = list(cursor)

    for idx, article in enumerate(docs, start=1):
        raw_body = article.get("body") or ""
        clean = normalize_body(raw_body)
        article["_clean_hash"] = sha256(clean) if clean else ""
        article["_domain"] = domain_from_url(article.get("url", ""))
        article["_gibberish"] = looks_paywalled_gibberish(raw_body)

        # Collect for duplicate clustering later
        all_docs.append(article)

        body_preview = raw_body[:BODY_PREVIEW_CHARS] + ("..." if len(raw_body) > BODY_PREVIEW_CHARS else "")
        wrapped_body = textwrap.fill(body_preview, width=LINE_WIDTH)

        print("=" * 80)
        print(f"[{idx}/{min(count, MAX_RESULTS_PER_TITLE)}]")
        print(f"Article ID: {article.get('article_id', 'N/A')}")
        print(f"Is Paywalled (system): {article.get('is_paywalled', 'N/A')}")
        print(f"Body looks paywalled/gibberish: {article.get('_gibberish', 'N/A')}")
        print(f"Title: {article.get('title', 'N/A')}")
        print(f"Normalized Title: {article.get('normalized_title', 'N/A')}")
        print(f"Published Date: {article.get('published_date', 'N/A')}")
        print(f"URL: {article.get('url', 'N/A')}")
        print(f"Domain: {article.get('_domain', 'N/A')}")
        print(f"Author: {article.get('author', 'N/A')}")
        print(f"Publisher: {article.get('publisher', 'N/A')}")
        print(f"Description: {article.get('description', 'N/A')}")
        print(f"\nBody Text (preview):\n{wrapped_body}\n")

# Dedup by article_id (same article pulled by multiple titles)
by_id = {}
for d in all_docs:
    if d.get("article_id"):
        by_id[d["article_id"]] = d
docs = list(by_id.values())

# ============================================================
# 2) Duplicate clustering to satisfy boss question
#    - exact duplicates by clean hash
#    - also show same normalized_title clusters
#    Apply paywall rule: need non-paywalled duplicates, unless 3+ copies.
# ============================================================

print("\n" + "=" * 110)
print("DUPLICATE SUMMARY (meets boss criteria)")
print("=" * 110)

if not docs:
    print("No docs pulled from your suspected titles. (Try shorter search phrases.)")
    raise SystemExit(0)

# A) Exact dup clusters by clean hash
hash_clusters = defaultdict(list)
for d in docs:
    h = d.get("_clean_hash") or ""
    if h:
        hash_clusters[h].append(d)

exact_dups = [c for c in hash_clusters.values() if len(c) >= 2]

# B) Same normalized_title clusters
title_clusters = defaultdict(list)
for d in docs:
    k = (strip_guest_prefix(d.get("normalized_title") or d.get("title") or "")).lower().strip()
    if k:
        title_clusters[k].append(d)

title_dups = [c for c in title_clusters.values() if len(c) >= 2]

def passes_boss_paywall_rule(cluster):
    non_pay = [x for x in cluster if x.get("is_paywalled") is False]
    if len(non_pay) >= 2:
        return True
    if ALLOW_ALL_PAYWALLED_IF_3PLUS and len(cluster) >= 3 and len(non_pay) >= 1:
        return True
    return False

def print_cluster(label, cluster):
    cluster = sorted(cluster, key=lambda x: (x.get("published_date",""), x.get("_domain","")))
    non_pay = [x for x in cluster if x.get("is_paywalled") is False]
    print("-" * 110)
    print(f"{label} | copies={len(cluster)} | non-paywalled={len(non_pay)}")
    print("Titles:")
    for t in sorted({x.get("normalized_title") or x.get("title") for x in cluster})[:5]:
        print(f"  - {t}")
    print("Copies:")
    for x in cluster:
        print(f"  - {x.get('article_id','N/A')} | {x.get('published_date','N/A')} | paywalled={x.get('is_paywalled','N/A')} | {x.get('_domain','N/A')}")
        print(f"    {x.get('url','N/A')}")

# Print exact dup clusters that satisfy boss paywall focus
kept = 0
for c in sorted(exact_dups, key=len, reverse=True):
    if passes_boss_paywall_rule(c):
        print_cluster("EXACT_DUP (same clean-body hash)", c)
        kept += 1

# If none found via exact hash, show normalized_title dup clusters (weaker)
if kept == 0:
    for c in sorted(title_dups, key=len, reverse=True):
        if passes_boss_paywall_rule(c):
            print_cluster("TITLE_DUP (same normalized_title)", c)
            kept += 1

if kept == 0:
    print("No non-paywalled duplicate clusters found among the pulled candidates (per system paywall flag).")
    print("This does NOT prove there are no duplicates in the whole report—only that none were found for these titles.")
else:
    print(f"\nFound {kept} duplicate cluster(s) that meet boss paywall criteria.")
print("\nDone.")
