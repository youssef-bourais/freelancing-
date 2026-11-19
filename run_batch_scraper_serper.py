#!/usr/bin/env python3
import csv
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata


def slugify(text: str) -> str:
  norm = unicodedata.normalize("NFKD", (text or "").casefold())
  stripped = "".join(ch for ch in norm if not unicodedata.combining(ch))
  return re.sub(r"[^a-z0-9]+", "_", stripped).strip("_")


def newest_scraped_csv(before_files: set[Path], scraper_dir: Path) -> Path | None:
  candidates = [
      p
      for p in scraper_dir.rglob("*.csv")
      if p.is_file() and p.resolve() not in before_files and "enriched" not in p.name
  ]
  if not candidates:
    return None
  return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> None:
  cities_csv = Path(sys.argv[1] if len(sys.argv) > 1 else "cities_spain.csv")
  scraper_dir = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
  enrich_script = Path(sys.argv[3] if len(sys.argv) > 3 else "enrich_serper.py")
  delay = float(sys.argv[4]) if len(sys.argv) > 4 else 0.8

  scrape_py = scraper_dir / "scripts" / "scrape.py"
  if not scrape_py.exists():
    sys.exit(f"Could not find {scrape_py}. Pass your google-maps-scraper-kit folder as arg 2.")
  if not enrich_script.exists():
    sys.exit(f"Could not find {enrich_script}. Pass path to enrich_serper.py as arg 3.")

  with open(cities_csv, mode="r", encoding="utf-8", newline="") as f:
    cities = list(csv.DictReader(f))

  out_dir = Path("enriched_cities")
  out_dir.mkdir(exist_ok=True)

  for idx, row in enumerate(cities, 1):
    city = row["city"].strip()
    label = (row.get("region_label") or f"{city}, Spain").strip()
    depth = (row.get("depth") or "5").strip()
    final_csv = out_dir / f"{slugify(city)}_enriched.csv"

    if final_csv.exists():
      print(f"[SKIP {idx}/{len(cities)}] {final_csv} already exists.")
      continue

    before = {p.resolve() for p in scraper_dir.rglob("*.csv")}
    scrape_cmd = [
        sys.executable,
        str(scrape_py),
        f"comercios en {city}",
        "--city",
        label,
        "--depth",
        depth,
        "--no-website",
    ]
    print(f"\n[SCRAPE {idx}/{len(cities)}] {' '.join(scrape_cmd)}")
    rc = subprocess.run(scrape_cmd, cwd=scraper_dir).returncode
    if rc != 0:
      print(f"[ERROR] Scraper failed for {city} (code {rc}).")
      sys.exit(rc)

    time.sleep(1)
    raw_csv = newest_scraped_csv(before, scraper_dir)
    if not raw_csv:
      print(f"[WARN] No new CSV detected for {city}; skipping Serper enrichment.")
      continue

    enrich_cmd = [
        sys.executable,
        str(enrich_script),
        str(raw_csv),
        "--city",
        city,
        "--country",
        "es",
        "--lang",
        "es",
        "--delay",
        str(delay),
        "--output",
        str(final_csv),
    ]
    print(f"[ENRICH {idx}/{len(cities)}] {' '.join(enrich_cmd)}")
    rc = subprocess.run(enrich_cmd).returncode
    if rc != 0:
      print(f"[ERROR] enrich_serper.py failed for {city} (code {rc}).")
      sys.exit(rc)


if __name__ == "__main__":
  main()
