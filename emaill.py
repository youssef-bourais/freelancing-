#!/usr/bin/env python3
"""
Hands-free cold email outreach script for `<country>_email_leads.csv`.
- Extracts country name from CSV filename (e.g., `spain`).
- Contacts Ollama (`qwen2.5:7b`) ONCE at startup to generate the email in that
  country's language.
- Checks `sent_email.log` before every send and skips already-logged emails.
- Sends via SMTP, appends newly sent emails to `sent_email.log`, and prints a
  final summary.
"""

import argparse
import csv
from email.message import EmailMessage
import os
from pathlib import Path
import random
import re
import smtplib
import ssl
import sys
import time
import requests

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Distinct ANSI colors per step
RESET = "\033[0m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
DIM = "\033[90m"
RED = "\033[91m"
BLUE = "\033[94m"


def colorize(text: str, color: str) -> str:
  return f"{color}{text}{RESET}"


def load_dotenv_if_present(env_path: Path) -> None:
  if not env_path.exists():
    return
  for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    k, v = line.split("=", 1)
    k = k.strip()
    v = v.strip().strip('"').strip("'")
    if k and k not in os.environ:
      os.environ[k] = v


def extract_country(csv_path: Path) -> str:
  stem = csv_path.stem.lower()
  cleaned = re.sub(
      r"(_email_leads|_emails_leads|_mail_leads|_email|_emails|_leads)$",
      "",
      stem,
  )
  country = cleaned.replace("_", " ").strip()
  if not country:
    sys.exit(
        colorize(f"Error: Cannot detect country from '{csv_path.name}'.", RED)
    )
  return country


def extract_clean_email_parts(raw: str, country: str) -> tuple[str, str]:
  """Extract clean SUBJECT and BODY from model output."""
  if not raw:
    return "", ""

  text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
  if "</think>" in text:
    text = text.split("</think>")[-1].strip()
  text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()

  matches = list(
      re.finditer(r"(?:^|\n)\s*SUBJECT\s*:", text, flags=re.IGNORECASE)
  )
  if matches:
    anchored = text[matches[-1].start() :].strip()
    subj_m = re.search(r"SUBJECT\s*:\s*(.+)", anchored, flags=re.IGNORECASE)
    body_m = re.search(
        r"BODY\s*:\s*(.+)", anchored, flags=re.IGNORECASE | re.DOTALL
    )
    subject = (
        subj_m.group(1).splitlines()[0].strip().strip('"').strip("'")
        if subj_m
        else ""
    )
    body = body_m.group(1).strip() if body_m else ""
    body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body).strip()
    if subject and body:
      return subject, body

  lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
  if len(lines) >= 2:
    subject = lines[0].strip('"').strip("'")
    body = "\n".join(lines[1:]).strip()
    return subject, body

  default_subject = (
      "Página web para {BUSINESS_NAME}"
      if country.lower() in ("spain", "espana", "españa")
      else "Website for {BUSINESS_NAME}"
  )
  return default_subject, text


def generate_startup_message(
    country: str,
    model: str,
    ollama_url: str,
) -> tuple[str, str]:
  """
  Runs ONCE at startup: asks Ollama to generate a message in the language of
  `country`. Returns (subject_template, body_template).
  """
  print(colorize(f"country : {country} | model : {model}", CYAN))
  print(
      colorize(
          f"ollama generate a message in the language of ({country})...",
          MAGENTA,
      )
  )

  prompt = (
      f"generate a message of this country language ({country})\n"
      "Translate and adapt this exact message for {BUSINESS_NAME} into the "
      "language of {country}, keeping {BUSINESS_NAME} intact and always "
      "ending the message with bourais business:\n\n"
      "Hi! I found your business on Google Maps and noticed you don't seem to "
      "have a website.\n\n"
      "I'm a web developer, and I think your business could benefit from a "
      "simple website where customers can discover your services, photos, "
      "opening hours and contact information.\n\n"
      "I'd be happy to create a personalized website demo for your business, "
      "completely free, with no obligation.\n\n"
      "Would you be interested in seeing it?\n\n"
      "Best regards!\n"
      "bourais business\n\n"
      f"Write ONLY in the main language of {country} (no English commentary).\n"
      "Format:\n"
      "SUBJECT: <short subject>\n"
      "BODY:\n"
      "<email body>"
  )

  # prompt = (
  #     f"generate a message of this country language ({country})\n"
  #     "Write a short, friendly cold outreach email offering to create a "
  #     "professional website for {BUSINESS_NAME}, which we found on Google Maps, "
  #     "to help them get more customers. Ask if they would like to see a quick "
  #     "example, and sign off as BOURAIS business.\n"
  #     f"Write ONLY in the main language of {country} (no English commentary).\n"
  #     "Format:\n"
  #     "SUBJECT: <short subject>\n"
  #     "BODY:\n"
  #     "<short email body>"
  # )

  payload = {
      "model": model,
      "stream": False,
      "messages": [
          {
              "role": "system",
              "content": (
                  "Output ONLY the final email in the requested country's "
                  "language using SUBJECT: and BODY:. Never explain your "
                  "reasoning or repeat instructions."
              ),
          },
          {"role": "user", "content": prompt},
      ],
      "options": {
          "temperature": 0.25,
          "num_predict": 300,
      },
  }

  try:
    resp = requests.post(ollama_url, json=payload, timeout=180)
    resp.raise_for_status()
    raw = (resp.json().get("message") or {}).get("content", "")
  except Exception as exc:
    sys.exit(colorize(f"Error: Ollama failed: {exc}", RED))

  subject, body = extract_clean_email_parts(raw, country)
  if not subject or not body:
    sys.exit(
        colorize(
            f"Error: Could not parse clean SUBJECT/BODY from Ollama output:\n{raw}",
            RED,
        )
    )

  #to check business name at the end
  
  if "{BUSINESS_NAME}" not in subject and "{BUSINESS_NAME}" not in body:
    subject = subject.replace("{business_name}", "{BUSINESS_NAME}")
    body = body.replace("{business_name}", "{BUSINESS_NAME}")
  return subject, body


def normalize_email(val: str) -> str:
  if not val:
    return ""
  s = val.strip()
  if s.lower().startswith("mailto:"):
    s = s[7:].split("?", 1)[0].strip()
  m = EMAIL_RE.search(s)
  return m.group(0).strip().lower() if m else ""


def resolve_log_paths(
    log_arg: str, script_dir: Path, csv_dir: Path
) -> tuple[Path, list[Path]]:
  p = Path(log_arg)
  if p.is_absolute():
    return p.resolve(), [p.resolve()]

  candidates: list[Path] = []
  for base in (Path.cwd(), script_dir, csv_dir):
    resolved = (base / p).resolve()
    if resolved not in candidates:
      candidates.append(resolved)

  existing = [c for c in candidates if c.exists()]
  primary = existing[0] if existing else (script_dir / p).resolve()
  return primary, candidates


def load_sent_set(log_paths: list[Path]) -> set[str]:
  sent: set[str] = set()
  for lp in log_paths:
    if not lp.exists():
      continue
    for line in lp.read_text(encoding="utf-8", errors="ignore").splitlines():
      addr = normalize_email(line)
      if addr:
        sent.add(addr)
  return sent


def save_sent_email(log_path: Path, sent_set: set[str], addr: str) -> None:
  norm = normalize_email(addr)
  if not norm:
    return
  sent_set.add(norm)
  log_path.parent.mkdir(parents=True, exist_ok=True)
  with open(log_path, mode="a", encoding="utf-8") as f:
    f.write(norm + "\n")
    f.flush()


def load_leads_from_csv(csv_path: Path) -> list[tuple[str, str]]:
  """Load (business_name, normalized_email) from CSV with or without headers."""
  leads: list[tuple[str, str]] = []
  seen: set[str] = set()

  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    reader = csv.reader(f, skipinitialspace=True)
    rows = [r for r in reader if any(cell.strip() for cell in r)]

  if not rows:
    return leads

  first_row_lower = [c.strip().lower() for c in rows[0]]
  has_header = any(
      h in ("email", "e-mail", "mail", "business name", "name", "business")
      for h in first_row_lower
  )

  if has_header:
    header_map = {h: idx for idx, h in enumerate(first_row_lower)}
    name_idx = next(
        (
            header_map[k]
            for k in ("business name", "name", "business")
            if k in header_map
        ),
        0,
    )
    email_idx = next(
        (
            header_map[k]
            for k in ("email", "e-mail", "mail")
            if k in header_map
        ),
        1 if len(rows[0]) > 1 else 0,
    )
    data_rows = rows[1:]
  else:
    name_idx = 0
    email_idx = 1 if len(rows[0]) > 1 else 0
    data_rows = rows

  for r in data_rows:
    raw_name = r[name_idx].strip() if name_idx < len(r) else ""
    raw_email = r[email_idx].strip() if email_idx < len(r) else ""
    if not raw_email:
      for cell in r:
        if EMAIL_RE.search(cell):
          raw_email = cell
          break
    addr = normalize_email(raw_email)
    name = raw_name or "there"
    if not addr or addr in seen:
      continue
    seen.add(addr)
    leads.append((name, addr))

  return leads


def count_csv_lines(csv_path: Path) -> int:
  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    reader = csv.reader(f, skipinitialspace=True)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
  if not rows:
    return 0
  first_lower = [c.strip().lower() for c in rows[0]]
  has_header = any(
      h in ("email", "e-mail", "mail", "business name", "name", "business")
      for h in first_lower
  )
  return len(rows[1:] if has_header else rows)


def send_email(
    smtp_host: str,
    smtp_port: int,
    sender: str,
    password: str,
    to_addr: str,
    subject: str,
    body: str,
) -> None:
  msg = EmailMessage()
  msg["From"] = sender
  msg["To"] = to_addr
  msg["Subject"] = subject
  msg.set_content(body)

  ctx = ssl.create_default_context()
  try:
    if smtp_port == 465:
      with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=30) as s:
        s.login(sender, password)
        s.send_message(msg)
    else:
      with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(sender, password)
        s.send_message(msg)
  except (
      smtplib.SMTPServerDisconnected,
      ConnectionResetError,
      OSError,
  ):
    time.sleep(2)
    with smtplib.SMTP_SSL(smtp_host, 465, context=ctx, timeout=30) as s:
      s.login(sender, password)
      s.send_message(msg)


def main() -> None:
  script_dir = Path(__file__).resolve().parent
  load_dotenv_if_present(script_dir / ".env")
  load_dotenv_if_present(Path(".env"))

  parser = argparse.ArgumentParser(
      description="Send cold emails using Ollama once at startup."
  )
  parser.add_argument("csv_file", help="Path to <country>_email_leads.csv")
  parser.add_argument("--model", default="qwen2.5:7b")
  parser.add_argument("--ollama-url", default=OLLAMA_CHAT_URL)
  parser.add_argument(
      "--smtp-host", default=os.getenv("SMTP_HOST", "smtp.gmail.com")
  )
  parser.add_argument(
      "--smtp-port", type=int, default=int(os.getenv("SMTP_PORT", "587"))
  )
  parser.add_argument(
      "--email",
      default=os.getenv("GMAIL_USER") or os.getenv("SMTP_USER") or "",
  )
  parser.add_argument(
      "--password",
      default=os.getenv("GMAIL_APP_PASSWORD") or os.getenv("SMTP_PASS") or "",
  )
  parser.add_argument(
      "--limit",
      type=int,
      default=0,
      help="Max emails to send (0 = auto-set to number of lines in CSV)",
  )
  parser.add_argument("--min-delay", type=float, default=25.0)
  parser.add_argument("--max-delay", type=float, default=60.0)
  parser.add_argument("--log-file", default="sent_email.log")
  parser.add_argument("--dry-run", action="store_true")
  args = parser.parse_args()

  csv_path = Path(args.csv_file).resolve()
  if not csv_path.exists():
    sys.exit(colorize(f"Error: File not found: {csv_path}", RED))

  if not args.dry_run and (not args.email or not args.password):
    sys.exit(colorize("Error: Missing GMAIL_USER or GMAIL_APP_PASSWORD.", RED))

  primary_log, all_logs = resolve_log_paths(
      args.log_file, script_dir, csv_path.parent
  )
  sent_set = load_sent_set(all_logs)
  print(
      colorize(
          f"log : {primary_log} (loaded {len(sent_set)} emails)",
          YELLOW,
      )
  )

  country = extract_country(csv_path)
  subject_tpl, body_tpl = generate_startup_message(
      country=country,
      model=args.model,
      ollama_url=args.ollama_url,
  )

  print(colorize(f"\nSubject: {subject_tpl}\n{body_tpl}\n", YELLOW))

  print(colorize(f"get email from {csv_path.name}", CYAN))
  all_leads = load_leads_from_csv(csv_path)

  sent_count = 0
  not_sent_count = 0
  total_leads = len(all_leads)

  csv_line_count = count_csv_lines(csv_path)

  # effective_limit = args.limit if args.limit > 0 else csv_line_count
  effective_limit = min(args.limit, 80) if args.limit > 0 else min(csv_line_count, 80)

  print(colorize(f"limit set to {effective_limit} (from {csv_path.name})", CYAN))

  for idx, (name, addr) in enumerate(all_leads, 1):
    if sent_count >= effective_limit:
      break

    print(colorize(f"check if {addr} is there in {primary_log.name}", YELLOW))
    if addr in sent_set:
      not_sent_count += 1
      print(colorize(f"skipped (already in {primary_log.name}): {addr}", DIM))
      if idx < total_leads and sent_count < effective_limit:
        print(colorize(f"next email ({idx + 1}/{total_leads})", CYAN))
      continue

    subject = subject_tpl.replace("{BUSINESS_NAME}", name)
    body = body_tpl.replace("{BUSINESS_NAME}", name)

    print(colorize(f"sending message to {addr}", GREEN))
    if not args.dry_run:
      try:
        send_email(
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            sender=args.email,
            password=args.password,
            to_addr=addr,
            subject=subject,
            body=body,
        )
        save_sent_email(primary_log, sent_set, addr)
        sent_count += 1
        print(colorize(f"sent and saved to {primary_log.name}: {addr}", GREEN))
      except Exception as exc:
        not_sent_count += 1
        print(colorize(f"not sent to {addr}: {exc}", RED))
        continue
    else:
      save_sent_email(primary_log, sent_set, addr)
      sent_count += 1

    if idx < total_leads and sent_count < effective_limit:
      print(colorize(f"next email ({idx + 1}/{total_leads})", CYAN))
      time.sleep(random.uniform(args.min_delay, args.max_delay))

  print(
      colorize(
          f"\nSummary -> sent: {sent_count} | not sent: {not_sent_count}",
          BLUE,
      )
  )


if __name__ == "__main__":
  main()
