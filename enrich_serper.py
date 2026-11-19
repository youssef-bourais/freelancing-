#!/usr/bin/env python3
"""
Enrich no-website businesses from a CSV with public Email, Instagram, and Facebook
using Serper (google.serper.dev), outputting a clean CSV with only:
Business name, Email, Instagram, Facebook.
"""

import argparse
import csv
import os
import re
import sys
import time
import unicodedata
from urllib.parse import urlparse

import requests

try:
  from dotenv import load_dotenv

  load_dotenv()
except ImportError:
  pass

SERPER_URL = "https://google.serper.dev/search"

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
IGNORED_EMAIL_DOMAINS = (
    "example.com",
    "domain.com",
    "email.com",
    "sentry.io",
    "wixpress.com",
    "facebook.com",
    "instagram.com",
    "google.com",
)

JUNK_SOCIAL_SEGMENTS = {
    "p",
    "reel",
    "reels",
    "stories",
    "explore",
    "accounts",
    "hashtag",
    "sharer",
    "sharer.php",
    "share",
    "share.php",
    "plugins",
    "dialog",
    "login",
    "privacy",
    "policies",
    "help",
    "legal",
    "about",
    "watch",
    "groups",
    "events",
    "permalink.php",
    "photo",
    "photo.php",
    "posts",
    "videos",
    "public",
    "tags",
    "marketplace",
    "search",
}


def fold(text: str) -> str:
  normalized = unicodedata.normalize("NFKD", (text or "").casefold())
  return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
  return re.findall(r"[a-z0-9]+", fold(text))


def clean_social_url(url: str, platform: str) -> str | None:
  """Normalize an Instagram or Facebook URL to the main profile/page URL."""
  if not url:
    return None
  parsed = urlparse(url.strip())
  host = (parsed.hostname or "").lower()
  segments = [s for s in parsed.path.split("/") if s]
  if not segments:
    return None

  first = segments[0].lower()
  if first in JUNK_SOCIAL_SEGMENTS:
    return None
  if len(segments) >= 2 and segments[1].lower() in JUNK_SOCIAL_SEGMENTS:
    return None

  if platform == "Instagram" and (
      host == "instagram.com" or host.endswith(".instagram.com")
  ):
    return f"https://www.instagram.com/{segments[0].lstrip('@')}"

  if platform == "Facebook" and (
      host in ("facebook.com", "fb.com")
      or host.endswith(".facebook.com")
      or host.endswith(".fb.com")
  ):
    if first == "profile.php" and "id=" in parsed.query:
      fb_id = parsed.query.split("id=")[1].split("&")[0]
      return (
          f"https://www.facebook.com/profile.php?id={fb_id}"
          if fb_id.isdigit()
          else None
      )
    if first == "pages" and len(segments) >= 3:
      return "https://www.facebook.com/" + "/".join(segments[:3])
    return f"https://www.facebook.com/{segments[0]}"

  return None


def serper_query(
    api_key: str, query: str, country: str = "es", lang: str = "es", num: int = 8
) -> list[dict]:
  """Run a Google search via Serper with retries."""
  headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
  payload = {"q": query, "gl": country.lower(), "hl": lang.lower(), "num": num}

  for attempt in range(4):
    try:
      resp = requests.post(
          SERPER_URL, headers=headers, json=payload, timeout=25
      )
      if resp.ok:
        return resp.json().get("organic", [])
      if resp.status_code in (401, 403):
        sys.exit(f"Fatal Serper API error ({resp.status_code}): {resp.text}")
      if resp.status_code not in (429, 500, 502, 503, 504):
        return []
    except requests.RequestException:
      pass
    time.sleep(2**attempt)
  return []


def name_matches(business_name: str, text: str) -> bool:
  """Check if meaningful words from the business name appear in the search result."""
  name_tokens = [w for w in tokenize(business_name) if len(w) > 2]
  if not name_tokens:
    return False
  result_tokens = set(tokenize(text))
  compact = "".join(tokenize(text))
  hits = sum(
      1
      for w in name_tokens
      if w in result_tokens or (len(w) >= 4 and w in compact)
  )
  return (hits / len(name_tokens)) >= 0.5


def find_social(
    api_key: str,
    business_name: str,
    city: str,
    platform: str,
    country: str,
    lang: str,
) -> str:
  domain = "instagram.com" if platform == "Instagram" else "facebook.com"
  results = serper_query(
      api_key, f'"{business_name}" {city} site:{domain}', country, lang
  )
  for item in results:
    profile = clean_social_url(item.get("link", ""), platform)
    if not profile:
      continue
    combined = (
        f"{item.get('title', '')} {item.get('snippet', '')}"
        f" {urlparse(profile).path}"
    )
    if name_matches(business_name, combined):
      return profile
  return ""


def find_email(
    api_key: str, business_name: str, city: str, country: str, lang: str
) -> str:
  """Search public Google snippets for an email associated with the business."""
  query = (
      f'"{business_name}" {city} (email OR correo OR contacto OR "@gmail.com"'
      ' OR "@hotmail.com")'
  )
  results = serper_query(api_key, query, country, lang)
  for item in results:
    combined = f"{item.get('title', '')} {item.get('snippet', '')}"
    if not name_matches(business_name, combined):
      continue
    for match in EMAIL_RE.findall(combined):
      email = match.lower()
      if not any(email.endswith(bad) for bad in IGNORED_EMAIL_DOMAINS):
        return email
  return ""


def detect_column(
    fieldnames: list[str], candidates: tuple[str, ...]
) -> str | None:
  lowered = {f.lower().strip(): f for f in fieldnames}
  for cand in candidates:
    if cand in lowered:
      return lowered[cand]
  return None


def main() -> None:
  parser = argparse.ArgumentParser(
      description=(
          "Enrich no-website businesses with Email, Instagram, and Facebook via"
          " Serper."
      )
  )
  parser.add_argument(
      "input_csv", help="Path to CSV from google-maps-scraper-kit"
  )
  parser.add_argument(
      "--city", required=True, help="City name (e.g., Cocentaina)"
  )
  parser.add_argument(
      "--country", default="es", help="Country code (default: es)"
  )
  parser.add_argument(
      "--lang", default="es", help="Language code (default: es)"
  )
  parser.add_argument(
      "--delay",
      type=float,
      default=0.8,
      help="Delay between API calls (seconds)",
  )
  parser.add_argument(
      "--output",
      default="enriched_no_website_leads.csv",
      help="Output CSV filename",
  )
  args = parser.parse_args()

  api_key = os.getenv("SERPER_API_KEY", "").strip()
  if not api_key:
    sys.exit("Error: SERPER_API_KEY not found in environment or .env file.")

  with open(args.input_csv, mode="r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    if not reader.fieldnames:
      sys.exit("Error: Empty CSV file.")

    name_col = detect_column(
        reader.fieldnames, ("name", "title", "business name", "business_name")
    )
    web_col = detect_column(
        reader.fieldnames, ("website", "site", "url", "website_url")
    )

    if not name_col:
      sys.exit(
          f"Error: Could not find business name column in {reader.fieldnames}"
      )

    rows_to_process = []
    for row in reader:
      website = (row.get(web_col) or "").strip() if web_col else ""
      if website:
        continue
      name = (row.get(name_col) or "").strip()
      if not name:
        continue
      rows_to_process.append({
          "Business name": name,
          "Email": "",
          "Instagram": "",
          "Facebook": "",
      })

  print(f"Loaded {len(rows_to_process)} businesses without a website.")

  for idx, lead in enumerate(rows_to_process, 1):
    bname = lead["Business name"]
    print(f"[{idx}/{len(rows_to_process)}] Searching: {bname}")

    lead["Email"] = find_email(
        api_key, bname, args.city, args.country, args.lang
    )
    time.sleep(args.delay)

    lead["Instagram"] = find_social(
        api_key, bname, args.city, "Instagram", args.country, args.lang
    )
    time.sleep(args.delay)

    lead["Facebook"] = find_social(
        api_key, bname, args.city, "Facebook", args.country, args.lang
    )
    time.sleep(args.delay)

    print(
        f"   -> Email: {lead['Email'] or 'None'} | "
        f"IG: {lead['Instagram'] or 'None'} | "
        f"FB: {lead['Facebook'] or 'None'}"
    )

  headers = ["Business name", "Email", "Instagram", "Facebook"]
  with open(args.output, mode="w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows_to_process)

  print(f"\nSaved {len(rows_to_process)} leads to '{args.output}'.")


if __name__ == "__main__":
  main()
