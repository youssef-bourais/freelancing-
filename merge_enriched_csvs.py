#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path

FIELDS = ["Business name", "Email", "Instagram", "Facebook"]


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("inputs", nargs="+", help="CSV files or glob patterns")
  parser.add_argument("-o", "--output", default="spain_enriched_leads.csv")
  args = parser.parse_args()

  seen: set[tuple[str, str, str]] = set()
  merged: list[dict[str, str]] = []

  for pattern in args.inputs:
    for path in sorted(Path(".").glob(pattern)):
      if not path.is_file() or path.resolve() == Path(args.output).resolve():
        continue
      with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
          name = (row.get("Business name") or "").strip()
          email = (row.get("Email") or "").strip()
          ig = (row.get("Instagram") or "").strip()
          fb = (row.get("Facebook") or "").strip()
          if not name:
            continue
          key = (name.casefold(), ig.casefold(), fb.casefold())
          if key in seen:
            continue
          seen.add(key)
          merged.append({
              "Business name": name,
              "Email": email,
              "Instagram": ig,
              "Facebook": fb,
          })

  with open(args.output, mode="w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(merged)

  print(f"Wrote {len(merged)} unique leads to {args.output}")


if __name__ == "__main__":
  main()
