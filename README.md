# LLM-Assisted Data Quality & Publisher Discovery Pipeline

Production-style data engineering pipeline for large-scale content dataset validation. Built during a consulting engagement; secrets, private data, and client-specific information have been removed.

## What This Demonstrates

- Production-style data engineering with concurrent I/O and checkpoint writes
- LLM-assisted adjudication (Claude API) for publisher validation and content scoring
- MongoDB-backed duplicate detection using hash similarity and tokenized title matching
- Multi-phase publisher discovery combining Google CSE, homepage scoring, and sitemap freshness checks
- Data quality workflows at scale: 500K+ domain records across 50 US states

## Architecture

```
Google CSE search
      |
      v
Homepage scoring (HTML feature extraction)
      |
      v
LLM validation (Claude API — publisher legitimacy)
      |
      v
Sitemap freshness check (XML / xml.gz parsing, sitemap index traversal)
      |
      v
MongoDB article validation (duplicate detection, paywall classification)
      |
      v
Final validated CSV output
```

## Modules

| File | Description |
|------|-------------|
| `publisher_discovery.py` | Full 5-phase pipeline: CSE search, homepage scoring, LLM validation, sitemap activity check, CSV output |
| `publisher_discovery_cse.py` | Lightweight CSE-only version — faster and cheaper, skips LLM validation |
| `update_sitemaps_fast.py` | Concurrent sitemap checker: 20 parallel workers, XML/xml.gz parsing, sitemap index traversal, checkpoint writes every 25 rows |
| `duplicate_detector.py` | MongoDB duplicate detection using SHA-256 hash similarity, tokenized title matching, and paywall-aware clustering rules |

> **Note:** `duplicate_detector.py` was previously named `Mongos.py`. Renamed for clarity.

## Publisher Discovery Pipeline (publisher_discovery.py)

### Phase 1 - CSE Search
Queries Google Custom Search Engine for candidate publisher domains matching target geography and content type.

### Phase 2 - Homepage Scoring
Fetches each candidate homepage and extracts HTML signals (byline patterns, article link density, publication date markers, ad-to-content ratio) to score legitimacy before spending LLM calls.

### Phase 3 - LLM Validation
Sends scored candidates to Claude API with structured prompts. Claude classifies each domain as a legitimate local news publisher, aggregator, or non-publisher, with reasoning. Only high-confidence publishers advance.

### Phase 4 - Sitemap Freshness Check
For validated publishers, checks sitemap.xml / sitemap_index.xml for recent `<lastmod>` dates. Filters out domains that haven't published in the target window.

### Phase 5 - Output
Writes final validated publisher list to CSV with domain, classification, confidence score, sitemap URL, and latest publication date.

## Sitemap Checker (update_sitemaps_fast.py)

- **20 parallel workers** via `ThreadPoolExecutor`
- Handles `sitemap.xml`, `sitemap_index.xml`, `sitemap.xml.gz`, and index-referenced child sitemaps
- Parses `<lastmod>` timestamps across all child sitemaps (capped at 12 children, 4000 URLs per urlset for speed)
- **Checkpoint writes every 25 rows** - safe to interrupt and resume
- Outputs: `sitemap_url`, `sitemap_status`, `sitemap_type`, `latest_lastmod`

```bash
python update_sitemaps_fast.py domains.csv
# output: domains_with_sitemaps.csv

python update_sitemaps_fast.py domains.csv validated_domains.csv
```

## Duplicate Detector (duplicate_detector.py)

Queries MongoDB for article duplicates within a rolling date window around a target date. Two detection methods:

1. **Exact duplicate** - SHA-256 hash of normalized body text
2. **Title duplicate** - tokenized title matching with stopword removal and apostrophe normalization

Applies paywall-aware clustering rules: flags clusters with 2+ non-paywalled copies, or 3+ total copies with at least 1 non-paywalled.

## Sample Output

See [`sample_output.csv`](sample_output.csv) for a representative slice. Key fields:

| domain | verdict | llm_reason | homepage_score | activity_status | sitemap_status | latest_lastmod |
|--------|---------|------------|---------------|----------------|---------------|---------------|
| inforum.com | PASS | Local newspaper covering Fargo-Moorhead area | 115 | active | 200 | 2025-09-10 |
| newscenter1.tv | PASS | Local news station with strong news indicators | 115 | active | 200 | 2025-12-30 |
| bismarcktribune.com | PASS | Local newspaper covering Bismarck and surrounding ND communities | 105 | unknown | 200 | 2025-12-31 |
| thedickinsonpress.com | PASS | Local newspaper covering Dickinson, ND area | 95 | active | 200 | 2017-06-27 |
| minotdailynews.com | PASS | Minot Daily News - local newspaper covering ND | 115 | unknown | N/A | - |

Each row is a publisher domain scored by homepage signals, validated by LLM, and enriched with sitemap freshness data. The `homepage_score` reflects cumulative evidence (RSS feeds, bylines, article density, news schema, etc.). The `activity_date` and `latest_lastmod` together determine whether a publisher is active within the target publication window.

## Setup

```bash
pip install anthropic pymongo requests google-api-python-client \
            beautifulsoup4 lxml pandas tqdm
```

**Environment variables required:**

```bash
export ANTHROPIC_API_KEY="your-key"
export GOOGLE_CSE_API_KEY="your-key"
export GOOGLE_CSE_ID="your-cse-id"
export MONGO_URI="your-mongodb-connection-string"
```

## Scale

- 500K+ domain records processed
- 50 US states covered
- 200 publisher candidates evaluated per state
- 677 publisher additions validated in a single expansion run
- 20 parallel workers for sitemap checking
- Checkpoint writes prevent data loss on interruption
