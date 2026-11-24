#!/usr/bin/env python3
"""
Fully Automated Country Lead Pipeline (Single Command):
1. Starts google-maps-scraper-kit engine (`scripts/engine.py start`).
2. Uses local Ollama to generate `<country>_cities.csv` (reused on resume).
3. Runs `scripts/scrape.py --no-website` for each city.
4. Runs `enrich_serper.py` for each city.
5. Merges all city CSVs and exports 3 separate deduplicated CSVs:
   - `<country>_email_leads.csv`
   - `<country>_instagram_leads.csv`
   - `<country>_facebook_leads.csv`
6. Stops the scraper engine (`scripts/engine.py stop`) on exit.
"""

import argparse
import csv
from io import StringIO
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata

import requests

try:
  from dotenv import load_dotenv

  load_dotenv()
except ImportError:
  pass

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"


def slugify(text: str) -> str:
  norm = unicodedata.normalize("NFKD", (text or "").casefold())
  stripped = "".join(ch for ch in norm if not unicodedata.combining(ch))
  return re.sub(r"[^a-z0-9]+", "_", stripped).strip("_")


def strip_thinking_blocks(text: str) -> str:
  return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_csv_block(raw_text: str) -> str:
  cleaned = strip_thinking_blocks(raw_text)
  fenced = re.search(
      r"```(?:csv)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE
  )
  if fenced:
    cleaned = fenced.group(1).strip()

  lines = []
  for line in cleaned.splitlines():
    s = line.strip()
    if not s or s.startswith("#"):
      continue
    lines.append(s)
  return "\n".join(lines)


def start_scraper_engine(scraper_dir: Path) -> None:
  """Automatically start google-maps-scraper-kit background engine."""
  engine_py = scraper_dir / "scripts" / "engine.py"
  if not engine_py.exists():
    sys.exit(f"Error: Could not find {engine_py}. Check --scraper-dir.")

  print("[Engine] Starting Google Maps scraper engine...")
  res = subprocess.run(
      [sys.executable, str(engine_py), "start"],
      cwd=str(scraper_dir),
      capture_output=True,
      text=True,
  )
  if res.stdout.strip():
    print(f"[Engine] {res.stdout.strip()}")
  if res.returncode != 0:
    err = res.stderr.strip() or res.stdout.strip()
    sys.exit(f"Error starting scraper engine (code {res.returncode}): {err}")
  time.sleep(2)


def stop_scraper_engine(scraper_dir: Path) -> None:
  """Best-effort stop of the scraper engine when the pipeline finishes."""
  engine_py = scraper_dir / "scripts" / "engine.py"
  if engine_py.exists():
    print("\n[Engine] Stopping Google Maps scraper engine...")
    subprocess.run(
        [sys.executable, str(engine_py), "stop"],
        cwd=str(scraper_dir),
        capture_output=True,
        text=True,
    )


def generate_cities_csv_with_ollama(
    country: str,
    count: int,
    depth: int,
    model: str,
    ollama_url: str,
    cities_csv_path: Path,
) -> list[dict[str, str]]:
  """Ask a local Ollama model to generate a CSV of cities for the given country."""
  prompt = (
      f"List {count} important cities and towns in {country} for local business"
      " lead generation.\n"
      "Output ONLY raw CSV with header: city,region_label,depth\n"
      "Rules:\n"
      "- No markdown explanations, no numbering, no extra columns.\n"
      "- 'city': clean city name.\n"
      f"- 'region_label': '<city>, {country}' (or '<city>, <Region>,"
      f" {country}').\n"
      f"- 'depth': {depth}\n"
  )

  payload = {
      "model": model,
      "stream": False,
      "think": False,
      "messages": [
          {
              "role": "system",
              "content": (
                  "You are a precise data formatter. Return strictly valid CSV"
                  " rows with header city,region_label,depth and nothing else."
              ),
          },
          {"role": "user", "content": prompt},
      ],
      "options": {"temperature": 0.2},
  }

  print(
      f"[Ollama] Generating {count} cities for '{country}' using model"
      f" '{model}'..."
  )
  try:
    resp = requests.post(ollama_url, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
  except requests.RequestException as exc:
    sys.exit(
        f"Error connecting to Ollama at {ollama_url}: {exc}\n"
        "Make sure Ollama is running (`ollama serve` or `systemctl start ollama`)."
    )

  content = (data.get("message") or {}).get("content", "")
  csv_text = extract_csv_block(content)

  parsed_rows: list[dict[str, str]] = []
  seen_cities: set[str] = set()

  for row in csv.DictReader(StringIO(csv_text)):
    city = (row.get("city") or "").strip().strip('"')
    if not city or city.lower() == "city":
      continue
    city_key = slugify(city)
    if not city_key or city_key in seen_cities:
      continue
    seen_cities.add(city_key)

    label = (
        row.get("region_label") or f"{city}, {country}"
    ).strip().strip('"')
    row_depth = (row.get("depth") or str(depth)).strip().strip('"')
    if not row_depth.isdigit():
      row_depth = str(depth)

    parsed_rows.append({
        "city": city,
        "region_label": label,
        "depth": row_depth,
    })

  if not parsed_rows:
    sys.exit(
        "Error: Ollama did not return parseable city rows.\n"
        f"Raw output was:\n{content[:600]}"
    )

  cities_csv_path.parent.mkdir(parents=True, exist_ok=True)
  with open(cities_csv_path, mode="w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["city", "region_label", "depth"])
    writer.writeheader()
    writer.writerows(parsed_rows)

  print(f"[Ollama] Saved {len(parsed_rows)} cities to '{cities_csv_path}'.")
  return parsed_rows


def load_existing_cities_csv(
    cities_csv_path: Path, default_country: str, default_depth: int
) -> list[dict[str, str]]:
  rows: list[dict[str, str]] = []
  with open(cities_csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    for r in csv.DictReader(f):
      city = (r.get("city") or "").strip()
      if not city:
        continue
      rows.append({
          "city": city,
          "region_label": (
              r.get("region_label") or f"{city}, {default_country}"
          ).strip(),
          "depth": (r.get("depth") or str(default_depth)).strip(),
      })
  return rows


def newest_scraped_csv(before_files: set[Path], scraper_dir: Path) -> Path | None:
  candidates = [
      p
      for p in scraper_dir.rglob("*.csv")
      if p.is_file()
      and p.resolve() not in before_files
      and "enriched" not in p.name
      and "_cities.csv" not in p.name
  ]
  if not candidates:
    return None
  return max(candidates, key=lambda p: p.stat().st_mtime)


def write_channel_csv(
    out_path: Path, field_name: str, rows: list[dict[str, str]]
) -> None:
  """Write a deduplicated CSV containing only Business name and one channel column."""
  seen: set[tuple[str, str]] = set()
  deduped: list[dict[str, str]] = []

  for r in rows:
    name = r["Business name"]
    val = r[field_name]
    if not val or val.casefold() in ("none", "not found"):
      continue
    key = (name.casefold(), val.casefold())
    if key in seen:
      continue
    seen.add(key)
    deduped.append({"Business name": name, field_name: val})

  out_path.parent.mkdir(parents=True, exist_ok=True)
  with open(out_path, mode="w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["Business name", field_name])
    writer.writeheader()
    writer.writerows(deduped)

  print(f"[Export] Wrote {len(deduped)} rows to '{out_path}'.")


def export_three_channel_csvs(
    city_csvs: list[Path], country_slug: str, out_dir: Path
) -> None:
  """Collect all city rows and export separate Email, Instagram, and Facebook CSVs."""
  all_rows: list[dict[str, str]] = []

  for path in city_csvs:
    if not path.exists():
      continue
    with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
      for row in csv.DictReader(f):
        name = (row.get("Business name") or "").strip()
        if not name:
          continue
        all_rows.append({
            "Business name": name,
            "Email": (row.get("Email") or "").strip(),
            "Instagram": (row.get("Instagram") or "").strip(),
            "Facebook": (row.get("Facebook") or "").strip(),
        })

  print("\n[Summary] Exporting separate files by contact channel...")
  write_channel_csv(
      out_dir / f"{country_slug}_email_leads.csv", "Email", all_rows
  )
  write_channel_csv(
      out_dir / f"{country_slug}_instagram_leads.csv", "Instagram", all_rows
  )
  write_channel_csv(
      out_dir / f"{country_slug}_facebook_leads.csv", "Facebook", all_rows
  )


def main() -> None:
  parser = argparse.ArgumentParser(
      description=(
          "All-in-one automation: start engine -> generate country cities via"
          " Ollama -> scrape no-website businesses -> enrich via Serper ->"
          " export separate Email, Instagram, and Facebook CSVs."
      )
  )
  parser.add_argument(
      "--country",
      required=True,
      help="Target country name (e.g., 'Spain', 'Portugal', 'France').",
  )
  parser.add_argument(
      "--country-code",
      default="es",
      help="2-letter country code for Serper 'gl' parameter (default: es).",
  )
  parser.add_argument(
      "--lang",
      default="es",
      help="2-letter language code for Serper 'hl' parameter (default: es).",
  )
  parser.add_argument(
      "--cities-count",
      type=int,
      default=20,
      help="Number of cities to generate with Ollama (default: 20).",
  )
  parser.add_argument(
      "--depth",
      type=int,
      default=5,
      help="Scroll depth for scripts/scrape.py (default: 5).",
  )
  parser.add_argument(
      "--delay",
      type=float,
      default=0.8,
      help="Delay in seconds between Serper requests (default: 0.8).",
  )
  parser.add_argument(
      "--model",
      default="qwen2.5:7b",
      help="Local Ollama model name (default: qwen2.5:7b).",
  )
  parser.add_argument(
      "--ollama-url",
      default=OLLAMA_CHAT_URL,
      help=f"Local Ollama chat endpoint (default: {OLLAMA_CHAT_URL}).",
  )
  parser.add_argument(
      "--scraper-dir",
      default=".",
      help="Path to google-maps-scraper-kit directory (default: .).",
  )
  parser.add_argument(
      "--enrich-script",
      default="enrich_serper.py",
      help="Path to enrich_serper.py (default: enrich_serper.py).",
  )
  parser.add_argument(
      "--refresh-cities",
      action="store_true",
      help="Regenerate the cities CSV even if it already exists.",
  )
  parser.add_argument(
      "--keep-engine",
      action="store_true",
      help="Leave the scraper engine running after the script finishes.",
  )
  parser.add_argument(
      "--output-dir",
      default=".",
      help=(
          "Directory where the 3 final CSV files are saved (default: current"
          " directory)."
      ),
  )
  args = parser.parse_args()

  if not os.getenv("SERPER_API_KEY", "").strip():
    sys.exit("Error: SERPER_API_KEY is not set in environment or .env.")

  scraper_dir = Path(args.scraper_dir).resolve()
  scrape_py = scraper_dir / "scripts" / "scrape.py"
  enrich_script = Path(args.enrich_script).resolve()

  if not scrape_py.exists():
    sys.exit(f"Error: Missing {scrape_py}. Check --scraper-dir.")
  if not enrich_script.exists():
    sys.exit(f"Error: Missing {enrich_script}. Check --enrich-script.")

  country_slug = slugify(args.country)
  
  #create country directory
  country_dir = Path(country_slug)
  country_dir.mkdir(parents=True, exist_ok=True)

  work_dir = Path("pipeline_runs") / country_slug
  cities_csv = work_dir / f"{country_slug}_cities.csv"
  city_out_dir = work_dir / "enriched_cities"
  city_out_dir.mkdir(parents=True, exist_ok=True)
  final_out_dir = Path(args.output_dir)
  

  # 1. Generate or load cities CSV
  if cities_csv.exists() and not args.refresh_cities:
    print(f"[Cities] Reusing existing '{cities_csv}'.")
    cities = load_existing_cities_csv(cities_csv, args.country, args.depth)
  else:
    cities = generate_cities_csv_with_ollama(
        country=args.country,
        count=args.cities_count,
        depth=args.depth,
        model=args.model,
        ollama_url=args.ollama_url,
        cities_csv_path=cities_csv,
    )

  # 2. Start engine -> Scrape each city -> Enrich each city -> Export 3 CSVs
  start_scraper_engine(scraper_dir)
  produced_city_csvs: list[Path] = []

  try:
    for idx, row in enumerate(cities, 1):
      city = row["city"]
      label = row["region_label"]
      city_depth = row["depth"]
      enriched_path = city_out_dir / f"{slugify(city)}_enriched.csv"
      produced_city_csvs.append(enriched_path)

      if enriched_path.exists():
        print(f"[SKIP {idx}/{len(cities)}] {enriched_path} already exists.")
        continue

      before_csvs = {p.resolve() for p in scraper_dir.rglob("*.csv")}
      # scrape_cmd = [
      #     sys.executable,
      #     str(scrape_py),
      #     f"comercios en {city}",
      #     "--city",
      #     label,
      #     "--depth",
      #     city_depth,
      #     "--no-website",
      # ]
      query_prefix = {
          "es": "comercios en",
          "en": "businesses in",
          "fr": "commerces à",
      }.get(args.lang.lower(), "businesses in")
      scrape_cmd = [
          sys.executable,
          str(scrape_py),
          f"{query_prefix} {city}",
          "--city",
          label,
          "--depth",
          city_depth,
          "--no-website",
      ]
      print(f"\n[SCRAPE {idx}/{len(cities)}] {' '.join(scrape_cmd)}")
      rc = subprocess.run(scrape_cmd, cwd=str(scraper_dir)).returncode
      # if rc != 0:
      #   print(f"[ERROR] Scraper exited with status {rc} for city '{city}'.")
      #   sys.exit(rc)
      if rc != 0:
        print(
            f"[WARN] Scraper returned 0 results / status {rc} for '{city}',"
            " skipping..."
        )
        time.sleep(5)
        continue

      time.sleep(1)
      raw_csv = newest_scraped_csv(before_csvs, scraper_dir)
      if not raw_csv:
        print(f"[WARN] No new CSV created for '{city}', skipping enrichment.")
        continue

      enrich_cmd = [
          sys.executable,
          str(enrich_script),
          str(raw_csv),
          "--city",
          city,
          "--country",
          args.country_code,
          "--lang",
          args.lang,
          "--delay",
          str(args.delay),
          "--output",
          str(enriched_path),
      ]
      print(f"[ENRICH {idx}/{len(cities)}] {' '.join(enrich_cmd)}")
      rc = subprocess.run(enrich_cmd).returncode
      if rc != 0:
        print(
            f"[ERROR] enrich_serper.py exited with status {rc} for city"
            f" '{city}'."
        )
        sys.exit(rc)

    export_three_channel_csvs(produced_city_csvs, country_slug, final_out_dir)
  finally:
    if not args.keep_engine:
      stop_scraper_engine(scraper_dir)


if __name__ == "__main__":
  main()
