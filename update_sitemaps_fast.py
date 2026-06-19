import csv
import gzip
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
import xml.etree.ElementTree as ET

# =========================
# CONFIG (tune as needed)
# =========================
DOMAIN_COL = "domain"          # change if your CSV uses a different column name
MAX_WORKERS = 20              # 10–30 is usually safe; start at 20
TIMEOUT = 6                   # seconds per request (fast; prevents hanging)
UA = "Mozilla/5.0 (compatible; SitemapUpdater/2.0)"
SITEMAP_PATHS = [
    "sitemap.xml",
    "sitemap_index.xml",
    "sitemap.xml.gz",
    "sitemap-index.xml",
    "sitemapindex.xml",
]
MAX_CHILD_SITEMAPS = 12       # speed cap for sitemap indexes
MAX_URLS_SCANNED = 4000       # cap per urlset for lastmod scanning
CHECKPOINT_EVERY = 25         # write partial output every N completed rows
# =========================


def normalize_domain(d: str) -> str:
    d = (d or "").strip().lower()
    if d.startswith("http://") or d.startswith("https://"):
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d


def fetch(session: requests.Session, url: str) -> requests.Response:
    headers = {"User-Agent": UA, "Accept": "*/*"}
    return session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)


def read_xml_bytes(resp: requests.Response) -> bytes:
    content = resp.content
    # decompress .gz if needed
    if resp.url.endswith(".gz") or "gzip" in resp.headers.get("Content-Type", "").lower():
        try:
            content = gzip.decompress(content)
        except Exception:
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as gz:
                    content = gz.read()
            except Exception:
                pass
    return content


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_lastmod(text: str):
    if not text:
        return None
    text = text.strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        # fallback: YYYY-MM-DD
        try:
            dt = datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None


def latest_lastmod_from_urlset(root) -> datetime | None:
    latest = None
    urls_seen = 0
    # iterate efficiently; cap to avoid huge sitemaps
    for el in root.iter():
        t = strip_ns(el.tag)
        if t == "url":
            urls_seen += 1
            if urls_seen > MAX_URLS_SCANNED:
                break
        elif t == "lastmod":
            dt = parse_lastmod(el.text or "")
            if dt and (latest is None or dt > latest):
                latest = dt
    return latest


def child_sitemaps_from_index(root) -> list[str]:
    children = []
    for el in root.iter():
        if strip_ns(el.tag) == "loc":
            loc = (el.text or "").strip()
            if loc.startswith("http"):
                children.append(loc)
                if len(children) >= MAX_CHILD_SITEMAPS:
                    break
    return children


def check_one_domain(domain: str) -> dict:
    """
    Returns:
      sitemap_url, sitemap_status, sitemap_type, latest_lastmod
    """
    if not domain:
        return {"sitemap_url": "N/A", "sitemap_status": "", "sitemap_type": "", "latest_lastmod": ""}

    bases = [f"https://{domain}/", f"http://{domain}/"]

    with requests.Session() as session:
        for base in bases:
            for path in SITEMAP_PATHS:
                url = urljoin(base, path)
                try:
                    r = fetch(session, url)
                    if r.status_code != 200:
                        continue

                    xml_bytes = read_xml_bytes(r)
                    root = ET.fromstring(xml_bytes)
                    root_tag = strip_ns(root.tag)

                    latest = None
                    sitemap_type = root_tag if root_tag in ("urlset", "sitemapindex") else ""

                    if root_tag == "urlset":
                        latest = latest_lastmod_from_urlset(root)

                    elif root_tag == "sitemapindex":
                        # Best-effort: sample up to MAX_CHILD_SITEMAPS child sitemaps
                        for child in child_sitemaps_from_index(root):
                            try:
                                cr = fetch(session, child)
                                if cr.status_code != 200:
                                    continue
                                cxml = read_xml_bytes(cr)
                                croot = ET.fromstring(cxml)
                                if strip_ns(croot.tag) == "urlset":
                                    dt = latest_lastmod_from_urlset(croot)
                                    if dt and (latest is None or dt > latest):
                                        latest = dt
                            except (requests.exceptions.Timeout,
                                    requests.exceptions.ConnectionError,
                                    requests.exceptions.ChunkedEncodingError,
                                    ET.ParseError):
                                continue
                            except Exception:
                                continue

                    return {
                        "sitemap_url": r.url,
                        "sitemap_status": str(r.status_code),
                        "sitemap_type": sitemap_type,
                        "latest_lastmod": latest.isoformat() if latest else "",
                    }

                except (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.ChunkedEncodingError,
                        ET.ParseError):
                    continue
                except Exception:
                    continue

    return {"sitemap_url": "N/A", "sitemap_status": "", "sitemap_type": "", "latest_lastmod": ""}


def update_csv(in_path: str, out_path: str):
    with open(in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    out_cols = ["sitemap_url", "sitemap_status", "sitemap_type", "latest_lastmod"]
    for c in out_cols:
        if c not in fieldnames:
            fieldnames.append(c)

    # Prepare jobs
    jobs = {}
    domains = []
    for idx, row in enumerate(rows):
        dom = normalize_domain(row.get(DOMAIN_COL, ""))
        domains.append(dom)

    completed = 0

    # Threaded sitemap checking
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for idx, dom in enumerate(domains):
            jobs[ex.submit(check_one_domain, dom)] = idx

        for fut in as_completed(jobs):
            idx = jobs[fut]
            try:
                res = fut.result()
            except Exception:
                res = {"sitemap_url": "N/A", "sitemap_status": "", "sitemap_type": "", "latest_lastmod": ""}

            rows[idx].update(res)
            completed += 1

            # progress + checkpoint
            if completed % CHECKPOINT_EVERY == 0 or completed == len(rows):
                print(f"Checked {completed}/{len(rows)} ...")
                # checkpoint write
                with open(out_path, "w", newline="", encoding="utf-8") as f_out:
                    w = csv.DictWriter(f_out, fieldnames=fieldnames)
                    w.writeheader()
                    w.writerows(rows)

    print(f"\n Done. Wrote updated CSV: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python update_sitemaps_fast.py <input.csv> [output.csv]")
        sys.exit(1)

    inp = sys.argv[1]
    if len(sys.argv) >= 3:
        out = sys.argv[2]
    else:
        base, ext = os.path.splitext(inp)
        out = f"{base}_with_sitemaps.csv"

    update_csv(inp, out)
