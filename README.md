# AI-Powered Content Validation & Publisher Discovery Pipeline

A production data pipeline built during a consulting engagement to validate LLM-generated content at scale. The system processes 500K+ domain records using a multi-phase approach combining rule-based filtering, concurrent I/O, and LLM-assisted adjudication via the Claude API.

## Overview

Three core modules work together to ingest, validate, and classify content and publisher data:

| Module | Purpose |
|--------|---------|
| `publisher_discovery.py` | Multi-phase pipeline to discover and validate local news publishers across all 50 US states |
| `publisher_discovery_cse.py` | CSE-only publisher discovery without LLM validation — faster, no API cost |
| `update_sitemaps_fast.py` | Concurrent sitemap freshness checker — enriches domain records with publication recency signals |
| `Mongos.py` | MongoDB duplicate detection and article validation tool with paywall-aware heuristics |

## publisher_discovery.py

Discovers and validates local news publishers using a 5-phase pipeline:

**Phase 1 — CSE Discovery:** Queries Google Custom Search across US cities to surface candidate publisher domains.

**Phase 2 — LLM Discovery:** Uses the Claude API to identify additional publishers not surfaced by search, batched by city.

**Phase 3 — Homepage Analysis:** Fetches and scores each candidate homepage using positive signals (RSS feeds, NewsArticle schema, bylines, news sections) and negative signals (restaurants, e-commerce, government sites, schools).

**Phase 4 — LLM Validation:** Sends candidate batches to Claude for binary PASS/FAIL classification with confidence scores and reasoning.

**Phase 5 — Activity Check:** Validates publisher recency by parsing sitemaps for latest publication dates.

Supports single-state and multi-state runs with compiled CSV output.

```bash
python publisher_discovery.py --state ME --api-key YOUR_CSE_KEY --cx YOUR_CX --anthropic-key YOUR_KEY
python publisher_discovery.py --states ME,NH,VT --limit 100 --require-sitemap-fresh
python publisher_discovery.py --state ALL  # runs all 50 states
```

## update_sitemaps_fast.py

Enriches a CSV of domains with sitemap freshness data using concurrent I/O.

- 20 parallel workers via `ThreadPoolExecutor`
- Handles `sitemap.xml`, `sitemap_index.xml`, `.gz` compressed sitemaps, and sitemap indexes with child traversal
- Checkpoints output every 25 rows to prevent data loss on large runs
- Extracts latest `<lastmod>` dates across up to 4,000 URLs per domain

```bash
python update_sitemaps_fast.py publishers_ME_100.csv publishers_ME_with_sitemaps.csv
```

## Mongos.py

Validates LLM-generated article references against a MongoDB article database.

- Token-based regex title matching with fuzzy normalization (handles contractions, prefixes, guest columns)
- SHA256 hash-based exact duplicate detection
- Paywall detection using readable character ratio heuristics and content length thresholds
- Configurable similarity thresholds and rolling publication date windows
- Clusters duplicates by both exact body hash and normalized title

```bash
# Configure CENTER_DATE and TITLES_FROM_LLLM at the top of the file, then:
python Mongos.py
```

## Setup

```bash
pip install pymongo requests
```

Set environment variables:
```bash
export MONGO_URI="your-mongodb-connection-string"
export ANTHROPIC_API_KEY="your-anthropic-key"
export CSE_API_KEY="your-google-cse-key"
export CSE_CX="your-cse-cx"
```

## Scale

- Processes 500K+ domain records
- Concurrent sitemap checking across thousands of publishers
- Multi-state publisher discovery covering all 50 US states
- LLM validation batched to minimize API calls and cost
