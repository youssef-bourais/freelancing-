#!/usr/bin/env python3
"""
Send personalized B2B outreach emails from `<country>_email_leads.csv`
(columns: Business name, Email) via SMTP, with resume tracking and rate limits.
"""

import argparse
import csv
from email.message import EmailMessage
import os
from pathlib import Path
import random
import smtplib
import ssl
import sys
import time

try:
  from dotenv import load_dotenv

  load_dotenv()
except ImportError:
  pass


def load_sent_log(log_path: Path) -> set[str]:
  if not log_path.exists():
    return set()
  return {
      line.strip().casefold()
      for line in log_path.read_text(encoding="utf-8").splitlines()
      if line.strip()
  }


def append_sent_log(log_path: Path, email_addr: str) -> None:
  with open(log_path, mode="a", encoding="utf-8") as f:
    f.write(email_addr.strip().casefold() + "\n")


def build_message(
    sender_name: str, sender_email: str, business_name: str, to_email: str
) -> EmailMessage:
  msg = EmailMessage()
  msg["Subject"] = f"Web profesional para {business_name}"
  msg["From"] = f"{sender_name} <{sender_email}>"
  msg["To"] = to_email

  body = f"""Hola equipo de {business_name},

He visto vuestro negocio en Google Maps y me he fijado en que todavía no tenéis página web propia.

En {sender_name} ayudamos a negocios locales a captar más clientes en Google con webs rápidas, modernas y adaptadas a móvil, sin complicaciones técnicas para vosotros.

¿Os gustaría que os envíe una breve propuesta o ejemplo de cómo quedaría vuestra web?

Un saludo,
{sender_name}
---
Si prefieres no recibir más correos nuestros, responde a este mensaje con "BAJA".

"""
  msg.set_content(body)
  return msg


def main() -> None:
  parser = argparse.ArgumentParser(
      description="Send outreach emails from CSV (Business name, Email)."
  )
  parser.add_argument("csv_file", help="Path to email leads CSV")
  parser.add_argument(
      "--limit",
      type=int,
      default=30,
      help="Max emails to send in this run (default: 30)",
  )
  parser.add_argument(
      "--min-delay",
      type=float,
      default=25.0,
      help="Minimum seconds between emails (default: 25)",
  )
  parser.add_argument(
      "--max-delay",
      type=float,
      default=55.0,
      help="Maximum seconds between emails (default: 55)",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Preview emails without actually sending",
  )
  args = parser.parse_args()

  smtp_host = os.getenv("SMTP_HOST", "").strip()
  smtp_port = int(os.getenv("SMTP_PORT", "465"))
  smtp_user = os.getenv("SMTP_USER", "").strip()
  smtp_pass = os.getenv("SMTP_PASS", "").strip()
  sender_name = os.getenv("SENDER_NAME", "BOURAIS busine").strip()

  if not args.dry_run and not all([smtp_host, smtp_user, smtp_pass]):
    sys.exit("Error: Set SMTP_HOST, SMTP_USER, and SMTP_PASS in .env first.")

  csv_path = Path(args.csv_file)
  if not csv_path.exists():
    sys.exit(f"Error: File not found: {csv_path}")

  log_path = Path("sent_emails.log")
  already_sent = load_sent_log(log_path)

  leads = []
  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
      name = (row.get("Business name") or "").strip()
      email = (row.get("Email") or "").strip()
      if name and email and email.casefold() not in already_sent:
        leads.append((name, email))

  print(f"Found {len(leads)} unsent email leads (cap: {args.limit}).")
  if not leads:
    return

  to_send = leads[: args.limit]

  if args.dry_run:
    for idx, (name, email) in enumerate(to_send, 1):
      msg = build_message(
          sender_name, smtp_user or "test@example.com", name, email
      )
      print(f"\n--- [DRY RUN {idx}/{len(to_send)}] To: {email} ---")
      print(msg.get_content())
    return

  context = ssl.create_default_context()
  with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
    server.login(smtp_user, smtp_pass)
    for idx, (name, email) in enumerate(to_send, 1):
      msg = build_message(sender_name, smtp_user, name, email)
      try:
        server.send_message(msg)
        append_sent_log(log_path, email)
        print(f"[SENT {idx}/{len(to_send)}] {name} -> {email}")
      except Exception as exc:
        print(f"[ERROR] Failed sending to {email}: {exc}")

      if idx < len(to_send):
        wait = random.uniform(args.min_delay, args.max_delay)
        print(f"   Waiting {wait:.1f}s...")
        time.sleep(wait)


if __name__ == "__main__":
  main()
