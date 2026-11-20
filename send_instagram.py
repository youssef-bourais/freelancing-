#!/usr/bin/env python3
"""
Hands-free Instagram DM automation for `<country>_instagram_leads.csv`
(columns: Business name, Instagram).
Automatically opens each profile, clicks Message, types a rotated Spanish
pitch with human-like delays, presses Send, and logs to `sent_instagram.log`.
"""

import argparse
import csv
from pathlib import Path
import random
import sys
import time
from playwright.sync_api import sync_playwright

DM_TEMPLATES = [
    (
        "¡Hola {name}! Vi vuestro negocio en Google Maps y me fijé en que"
        " todavía no tenéis página web. En BOURAIS busine creamos webs rápidas"
        " y profesionales para negocios locales para ayudaros a captar más"
        " clientes en Google. ¿Os gustaría que os pase más info?"
    ),
    (
        "Buenas {name}, estuve viendo vuestro perfil tras encontraros en"
        " Google Maps. Noté que aún no contáis con web propia y hoy mucha gente"
        " busca en Google antes de visitar un local. ¿Os interesaría ver un"
        " ejemplo de cómo quedaría vuestra web?"
    ),
    (
        "¡Hola equipo de {name}! Os encontré en Google Maps y vi que todavía"
        " no tenéis web. Ayudamos a negocios locales a tener presencia"
        " profesional en Google sin complicaciones técnicas. ¿Os puedo enviar"
        " una propuesta rápida por aquí?"
    ),
    (
        "Hola {name}, vi vuestro negocio en Google Maps y me pareció genial,"
        " aunque vi que aún no tenéis página web. Nos dedicamos a crear webs"
        " modernas para negocios locales. ¿Os gustaría que os cuente cómo"
        " trabajamos?"
    ),
]


def load_sent_log(log_path: Path) -> set[str]:
  if not log_path.exists():
    return set()
  return {
      line.strip().casefold()
      for line in log_path.read_text(encoding="utf-8").splitlines()
      if line.strip()
  }


def append_sent_log(log_path: Path, url: str) -> None:
  with open(log_path, mode="a", encoding="utf-8") as f:
    f.write(url.strip().casefold() + "\n")


def clean_ig_url(url: str) -> str:
  return url.split("?")[0].rstrip("/")


def is_logged_in(page) -> bool:
  """Check if Instagram session is active (no login form on screen)."""
  return page.locator("input[name='username']").count() == 0


def has_block_or_challenge(page) -> bool:
  """Detect Instagram checkpoint, suspension, or 'Try Again Later' popups."""
  url = page.url.lower()
  if "challenge" in url or "accounts/login" in url or "suspended" in url:
    return True
  body_text = (page.locator("body").inner_text() or "").lower()
  warning_phrases = [
      "try again later",
      "inténtalo de nuevo más tarde",
      "we restrict certain activity",
      "restringimos determinada actividad",
      "confirm it's you",
      "confirma que eres tú",
  ]
  return any(phrase in body_text for phrase in warning_phrases)


def main() -> None:
  parser = argparse.ArgumentParser(
      description="Hands-free Instagram DM sender from CSV."
  )
  parser.add_argument("csv_file", help="Path to Instagram leads CSV")
  parser.add_argument(
      "--limit",
      type=int,
      default=15,
      help="Max DMs to send in this run (default: 15)",
  )
  parser.add_argument(
      "--min-delay",
      type=float,
      default=45.0,
      help="Minimum seconds to wait between DMs (default: 45)",
  )
  parser.add_argument(
      "--max-delay",
      type=float,
      default=105.0,
      help="Maximum seconds to wait between DMs (default: 105)",
  )
  parser.add_argument(
      "--profile-dir",
      default="./pw_instagram_profile",
      help="Persistent Chrome session folder",
  )
  parser.add_argument(
      "--use-chrome",
      action="store_true",
      help="Use installed Google Chrome binary instead of bundled Chromium",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Type the message in the box without pressing Enter to send",
  )
  args = parser.parse_args()

  csv_path = Path(args.csv_file)
  if not csv_path.exists():
    sys.exit(f"Error: File not found: {csv_path}")

  log_path = Path("sent_instagram.log")
  already_sent = load_sent_log(log_path)

  leads = []
  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
      name = (row.get("Business name") or "").strip()
      raw_url = (row.get("Instagram") or "").strip()
      if not name or not raw_url:
        continue
      ig_url = clean_ig_url(raw_url)
      if "/p/" in ig_url or "/reel/" in ig_url or "/explore/" in ig_url:
        continue
      if ig_url.casefold() not in already_sent:
        already_sent.add(ig_url.casefold())
        leads.append((name, ig_url))

  print(f"Found {len(leads)} unsent Instagram profiles (cap: {args.limit}).")
  if not leads:
    return

  batch = leads[: args.limit]
  launch_kwargs = {
      "user_data_dir": args.profile_dir,
      "headless": False,
      "viewport": {"width": 1280, "height": 850},
  }
  if args.use_chrome:
    launch_kwargs["channel"] = "chrome"

  with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(**launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    time.sleep(4)

    if not is_logged_in(page):
      print(
          "\n[First-Time Login] Please log into Instagram in the browser"
          " window. Waiting up to 3 minutes..."
      )
      page.wait_for_selector("input[name='username']", state="detached", timeout=180000)
      time.sleep(3)
      print("[Login] Session saved! Starting hands-free outreach...")

    sent_count = 0
    for idx, (name, ig_url) in enumerate(batch, 1):
      print(f"\n[{idx}/{len(batch)}] Visiting {name}: {ig_url}")
      try:
        page.goto(ig_url, wait_until="domcontentloaded", timeout=30000)
      except Exception as exc:
        print(f"   [SKIP] Failed to load page: {exc}")
        continue

      time.sleep(random.uniform(3.5, 6.0))

      if has_block_or_challenge(page):
        print(
            "\n[STOP] Instagram security challenge or rate-limit detected."
            " Stopping safely to protect your account."
        )
        break

      # Click "Message" / "Enviar mensaje" button on the profile
      msg_btn = page.locator(
          "div[role='button']:has-text('Message'),"
          " div[role='button']:has-text('Enviar mensaje')"
      ).first

      if msg_btn.count() == 0:
        print(f"   [SKIP] No Message button on {name} (private or DMs closed).")
        append_sent_log(log_path, ig_url)
        continue

      try:
        msg_btn.click(timeout=6000)
        time.sleep(random.uniform(3.0, 5.0))
      except Exception as exc:
        print(f"   [SKIP] Could not click Message button: {exc}")
        continue

      # Dismiss "Turn on Notifications" popup if Instagram shows it
      not_now_btn = page.locator(
          "button:has-text('Not Now'), button:has-text('Ahora no')"
      ).first
      if not_now_btn.count() > 0:
        try:
          not_now_btn.click(timeout=2000)
          time.sleep(1)
        except Exception:
          pass

      # Locate DM input box and type message naturally
      chat_box = page.locator("div[role='textbox'][contenteditable='true']").last
      if chat_box.count() == 0:
        print(f"   [SKIP] DM text box did not open for {name}.")
        continue

      dm_text = random.choice(DM_TEMPLATES).format(name=name)
      try:
        chat_box.click()
        time.sleep(random.uniform(0.6, 1.4))
        chat_box.press_sequentially(dm_text, delay=random.randint(25, 55))
        time.sleep(random.uniform(1.0, 2.0))

        if args.dry_run:
          print(f"   [DRY RUN] Typed message for {name} (not sent).")
        else:
          page.keyboard.press("Enter")
          time.sleep(2.5)
          append_sent_log(log_path, ig_url)
          sent_count += 1
          print(f"   [SENT {sent_count}] Messaged {name}!")
      except Exception as exc:
        print(f"   [ERROR] Failed typing/sending to {name}: {exc}")
        continue

      if idx < len(batch):
        wait = random.uniform(args.min_delay, args.max_delay)
        print(f"   Waiting {wait:.1f}s before next profile...")
        time.sleep(wait)

    print(f"\n[Done] Sent {sent_count} Instagram DMs in this session.")
    context.close()


if __name__ == "__main__":
  main()

    
