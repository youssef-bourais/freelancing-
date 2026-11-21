#!/usr/bin/env python3
"""
Facebook Page outreach script (`<country>_facebook_leads.csv`).
1. Extracts country name from CSV filename (e.g., `spain`).
2. Calls Qwen (via Ollama) ONCE at startup to detect the country's language
   and generate the outreach message template offering a website.
3. For each lead not in `sent_facebook.log`: fills in the business name,
   opens the page, clicks Message, types & sends, closes chat, and logs the URL.
"""

import argparse
import csv
from pathlib import Path
import random
import re
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from playwright.sync_api import sync_playwright
import requests

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"


def extract_country_from_filename(csv_path: Path) -> str:
  """Extract country name from filenames like `spain_facebook_leads.csv`."""
  stem = csv_path.stem.lower()
  cleaned = re.sub(
      r"(_facebook_leads|_fb_leads|_facebook|_leads|_fb)$", "", stem
  )
  country = cleaned.replace("_", " ").strip()
  if not country:
    sys.exit(
        f"Error: Could not extract country from '{csv_path.name}'. "
        "Name the file like '<country>_facebook_leads.csv'."
    )
  return country


def clean_qwen_response(raw_text: str) -> str:
  """Remove reasoning tags/lines and return a single clean message."""
  if not raw_text:
    return ""

  text = raw_text.strip()
  text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
  if "</think>" in text:
    text = text.split("</think>")[-1].strip()

  lines = []
  for line in text.splitlines():
    s = line.strip()
    if not s:
      continue
    low = s.lower()
    if low == "think" or low.startswith((
        "thinking",
        "thought:",
        "analysis:",
        "note:",
        "translation:",
        "here is",
    )):
      continue
    lines.append(s)

  msg = " ".join(lines).strip()
  msg = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", msg).strip()
  msg = msg.strip('"').strip("'").strip()
  msg = re.sub(r"\s+", " ", msg).strip()
  return msg


def fallback_template_for_country(country: str) -> str:
  """Safe fallback template using {BUSINESS_NAME} if Qwen fails."""
  c = country.lower().strip()
  if c in ("spain", "espana", "españa"):
    return (
        "Hola {BUSINESS_NAME}, hemos encontrado vuestro negocio en Google Maps "
        "y os ofrecemos crear una página web profesional para conseguir más "
        "clientes. ¿Os gustaría ver un ejemplo?"
    )
  if c in (
      "france",
      "maroc",
      "morocco",
      "belgium",
      "belgique",
      "algeria",
      "tunisia",
  ):
    return (
        "Bonjour {BUSINESS_NAME}, nous avons trouvé votre page sur Google Maps "
        "et vous proposons la création d'un site web professionnel pour "
        "attirer plus de clients. Souhaitez-vous voir un exemple ?"
    )
  return (
      "Hello {BUSINESS_NAME}, we found your business on Google Maps and "
      "offer to create a professional website to help you get more "
      "customers. Would you like to see an example?"
  )


def generate_startup_template(
    country: str,
    model: str,
    ollama_url: str,
) -> str:
  """Called ONCE at startup: detect country's language and generate base template."""
  fallback = fallback_template_for_country(country)
  prompt = (
      f"Country: {country}\n"
      "Detect the main language of this country and write ONE short, friendly "
      "Facebook outreach message in that language.\n"
      "Requirements:\n"
      "- Keep the exact placeholder {BUSINESS_NAME} where the business name goes.\n"
      "- Mention that we found {BUSINESS_NAME} on Google Maps and offer to "
      "create a professional website to help get more customers.\n"
      "- Keep it to 2 short sentences on a single line.\n"
      "- Output ONLY the message text with no notes or explanations."
  )
  payload = {
      "model": model,
      "stream": False,
      "think": False,
      "messages": [
          {
              "role": "system",
              "content": (
                  "You write short outreach messages in the target country's "
                  "language. Output only the final message text."
              ),
          },
          {"role": "user", "content": prompt},
      ],
      "options": {
          "temperature": 0.2,
          "num_predict": 120,
      },
  }

  try:
    resp = requests.post(ollama_url, json=payload, timeout=150)
    resp.raise_for_status()
    raw = (resp.json().get("message") or {}).get("content", "")
    cleaned = clean_qwen_response(raw)
    if not cleaned or len(cleaned) > 450:
      print("[Startup Template] Qwen output empty/too long -> using fallback.")
      return fallback
    if "{BUSINESS_NAME}" not in cleaned:
      cleaned = cleaned.replace("{business_name}", "{BUSINESS_NAME}")
      if "{BUSINESS_NAME}" not in cleaned:
        cleaned = f"{cleaned} ({'{BUSINESS_NAME}'})"
    return cleaned
  except Exception as exc:
    print(f"[Startup Template] Ollama warning ({exc}) -> using fallback.")
    return fallback


def normalize_fb_url(raw_url: str) -> str:
  """Normalize Facebook URLs for consistent deduplication in `sent_facebook.log`."""
  s = raw_url.strip()
  if not s:
    return ""
  if not s.startswith(("http://", "https://")):
    s = "https://" + s
  parsed = urlparse(s)
  host = parsed.netloc.lower()
  if host.startswith(("www.", "m.", "web.", "mbasic.")):
    host = "facebook.com"
  path = parsed.path.rstrip("/")
  if path == "/profile.php":
    qs = parse_qs(parsed.query)
    if "id" in qs:
      clean_query = urlencode({"id": qs["id"][0]})
      return urlunparse(
          ("https", host, "/profile.php", "", clean_query, "")
      ).lower()
  return urlunparse(("https", host, path, "", "", "")).lower()


def load_sent_log(log_path: Path) -> set[str]:
  sent: set[str] = set()
  if not log_path.exists():
    return sent
  for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    entry = line.strip()
    if entry:
      sent.add(entry.casefold())
      norm = normalize_fb_url(entry)
      if norm:
        sent.add(norm.casefold())
  return sent


def append_sent_log(log_path: Path, fb_url: str) -> None:
  with open(log_path, mode="a", encoding="utf-8") as f:
    f.write(fb_url.strip().casefold() + "\n")


def is_valid_page_url(url: str) -> bool:
  bad_segments = [
      "/posts/",
      "/photos/",
      "/videos/",
      "/reel/",
      "/groups/",
      "/events/",
      "/share/",
      "/sharer.php",
  ]
  return not any(seg in url.lower() for seg in bad_segments)


def is_logged_in(page) -> bool:
  return (
      page.locator("input[name='email']").count() == 0
      and page.locator("input[name='pass']").count() == 0
  )


def has_account_block(page) -> tuple[bool, str]:
  url = page.url.lower()
  for k in ("/checkpoint", "/login", "/recover", "/suspended"):
    if k in url:
      return True, f"URL matched '{k}' ({page.url})"

  dialog_text = " ".join(
      page.locator("div[role='dialog']").all_inner_texts()
  ).lower()
  for phrase in (
      "you can't send messages right now",
      "no puedes enviar mensajes en este momento",
      "vous ne pouvez pas envoyer de messages pour le moment",
      "temporarily blocked from using messenger",
      "bloqueado temporalmente",
  ):
    if phrase in dialog_text:
      return True, f"Messenger restriction dialog: '{phrase}'"

  return False, ""


def dismiss_unsent_popup_if_any(page) -> None:
  """If Facebook shows a discard/unsent dialog when closing chat, cancel/dismiss safely."""
  dialogs = page.locator("div[role='dialog']")
  if dialogs.count() == 0:
    return
  txt = " ".join(dialogs.all_inner_texts()).lower()
  if any(
      w in txt
      for w in ("discard", "descartar", "didn't send", "no se envió", "ignorer")
  ):
    cancel_btns = dialogs.locator(
        "div[role='button']:has-text('Cancel'), "
        "div[role='button']:has-text('Cancelar'), "
        "div[role='button']:has-text('Keep'), "
        "div[role='button']:has-text('Conservar')"
    )
    if cancel_btns.count() > 0:
      try:
        cancel_btns.first.click(timeout=1500)
        time.sleep(0.5)
      except Exception:
        pass


def send_message_and_verify(page, chat_box, message_text: str) -> bool:
  """Type `message_text`, press Send/Enter once, and wait for send to finish."""
  chat_box.click(timeout=5000)
  time.sleep(0.5)
  chat_box.press_sequentially(message_text, delay=random.randint(20, 45))
  time.sleep(0.8)

  page.keyboard.press("Enter")
  time.sleep(2.0)

  remaining = (chat_box.inner_text() or "").strip()
  if remaining:
    send_btns = page.locator(
        "div[aria-label='Press Enter to send'][role='button'], "
        "div[aria-label='Pulsa Intro para enviar'][role='button'], "
        "div[aria-label='Send'][role='button'], "
        "div[aria-label='Enviar'][role='button'], "
        "div[aria-label='Envoyer'][role='button'], "
        "button[aria-label='Send'], "
        "button[aria-label='Enviar']"
    )
    if send_btns.count() > 0:
      try:
        send_btns.last.click(timeout=2500)
        time.sleep(2.0)
      except Exception:
        pass

  time.sleep(1.5)
  return True


def close_messenger_chat(page) -> None:
  """Close open Messenger chat popups cleanly after sending."""
  dismiss_unsent_popup_if_any(page)

  close_selectors = [
      "div[aria-label='Close chat'][role='button']",
      "div[aria-label='Cerrar chat'][role='button']",
      "div[aria-label='Fermer la discussion'][role='button']",
      "div[aria-label='Fechar chat'][role='button']",
      "div[aria-label='Chat schließen'][role='button']",
      "div[aria-label='Chiudi chat'][role='button']",
      "button[aria-label='Close chat']",
      "button[aria-label='Cerrar chat']",
  ]
  selector = ", ".join(close_selectors)

  for _ in range(3):
    btns = page.locator(selector)
    if btns.count() == 0:
      break
    clicked = False
    for i in range(btns.count() - 1, -1, -1):
      btn = btns.nth(i)
      try:
        if btn.is_visible():
          btn.click(timeout=2000)
          time.sleep(0.6)
          dismiss_unsent_popup_if_any(page)
          clicked = True
          break
      except Exception:
        pass
    if not clicked:
      break


def main() -> None:
  parser = argparse.ArgumentParser(
      description=(
          "Send Facebook Page messages using country name from CSV filename "
          "and Qwen once at startup."
      )
  )
  parser.add_argument("csv_file", help="Path to <country>_facebook_leads.csv")
  parser.add_argument(
      "--model",
      default="qwen3:4b",
      help="Local Ollama model name (default: qwen3:4b)",
  )
  parser.add_argument(
      "--ollama-url",
      default=OLLAMA_CHAT_URL,
      help=f"Ollama chat API URL (default: {OLLAMA_CHAT_URL})",
  )
  parser.add_argument(
      "--limit",
      type=int,
      default=15,
      help="Max pages to process in this run (default: 15)",
  )
  parser.add_argument(
      "--min-delay",
      type=float,
      default=45.0,
      help="Minimum delay in seconds between pages (default: 45)",
  )
  parser.add_argument(
      "--max-delay",
      type=float,
      default=105.0,
      help="Maximum delay in seconds between pages (default: 105)",
  )
  parser.add_argument(
      "--profile-dir",
      default="./pw_facebook_profile",
      help="Persistent Chrome profile directory",
  )
  parser.add_argument(
      "--use-chrome",
      action="store_true",
      help="Use installed Google Chrome instead of bundled Chromium",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Type message in chat without sending",
  )
  args = parser.parse_args()

  csv_path = Path(args.csv_file)
  if not csv_path.exists():
    sys.exit(f"Error: File not found: {csv_path}")

  country = extract_country_from_filename(csv_path)
  print(f"[Country] {country} | [Model] {args.model}")

  # Generate the message template ONCE at the beginning of the script
  message_template = generate_startup_template(
      country=country,
      model=args.model,
      ollama_url=args.ollama_url,
  )
  print(f"[Startup Template] {message_template}")

  log_path = Path("sent_facebook.log")
  sent_entries = load_sent_log(log_path)
  print(f"[Log] Loaded {len(sent_entries)} entries from {log_path}")

  leads = []
  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
      name = (row.get("Business name") or "").strip()
      raw_url = (row.get("Facebook") or "").strip()
      if not name or not raw_url:
        continue
      norm_url = normalize_fb_url(raw_url)
      if not norm_url or not is_valid_page_url(norm_url):
        continue

      if (
          norm_url.casefold() in sent_entries
          or raw_url.strip().casefold() in sent_entries
      ):
        print(f"[SKIP - Already in {log_path}] {name} ({norm_url})")
        continue

      sent_entries.add(norm_url.casefold())
      leads.append((name, norm_url))

  print(f"[Queue] {len(leads)} unsent pages (limit: {args.limit})")
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
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
    time.sleep(4)

    if not is_logged_in(page):
      print(
          "\n[Login] Please log into Facebook in Chrome. Waiting up to 3"
          " minutes..."
      )
      page.wait_for_selector(
          "input[name='email']", state="detached", timeout=180000
      )
      time.sleep(3)

    close_messenger_chat(page)

    sent_count = 0
    for idx, (name, fb_url) in enumerate(batch, 1):
      print(f"\n[{idx}/{len(batch)}] {name} -> {fb_url}")
      close_messenger_chat(page)

      # Reuse the single startup template without calling Qwen again
      message_text = message_template.replace("{BUSINESS_NAME}", name)
      print(f"   [Message] {message_text}")

      try:
        page.goto(fb_url, wait_until="domcontentloaded", timeout=30000)
      except Exception as exc:
        print(f"   [SKIP] Navigation error: {exc}")
        continue

      time.sleep(random.uniform(4.0, 6.5))

      blocked, reason = has_account_block(page)
      if blocked:
        print(f"\n[STOP] Stopped: {reason}")
        break

      msg_btn = page.locator(
          "div[aria-label='Message'][role='button'],"
          " div[aria-label='Enviar mensaje'][role='button'],"
          " div[aria-label='Contacter'][role='button'],"
          " div[aria-label='Envoyer un message'][role='button'],"
          " div[aria-label='Nachricht senden'][role='button'],"
          " div[aria-label='Invia un messaggio'][role='button'],"
          " a[aria-label='Message'],"
          " a[aria-label='Enviar mensaje']"
      ).first

      if msg_btn.count() == 0:
        print(f"   [SKIP] No Message button on {name}; logging to {log_path}.")
        append_sent_log(log_path, fb_url)
        continue

      try:
        msg_btn.click(timeout=6000)
        time.sleep(random.uniform(3.0, 5.0))
      except Exception as exc:
        print(f"   [SKIP] Failed clicking Message button: {exc}")
        continue

      blocked, reason = has_account_block(page)
      if blocked:
        print(f"\n[STOP] Stopped after opening chat: {reason}")
        break

      chat_box = page.locator(
          "div[aria-label='Message'][role='textbox'],"
          " div[aria-label='Mensaje'][role='textbox'],"
          " div[aria-label='Messaggio'][role='textbox'],"
          " div[aria-label='Nachricht'][role='textbox'],"
          " div[aria-label='Mensagem'][role='textbox'],"
          " div[role='textbox'][contenteditable='true']"
      ).last

      if chat_box.count() == 0:
        print(f"   [SKIP] Chat input box not found for {name}.")
        close_messenger_chat(page)
        continue

      try:
        if args.dry_run:
          chat_box.click(timeout=5000)
          time.sleep(0.5)
          chat_box.press_sequentially(
              message_text, delay=random.randint(20, 45)
          )
          print(f"   [DRY RUN] Typed message for {name} (not sent).")
        else:
          send_message_and_verify(page, chat_box, message_text)
          append_sent_log(log_path, fb_url)
          sent_count += 1
          print(f"   [SENT {sent_count}] Logged {fb_url} to {log_path}.")
      except Exception as exc:
        print(f"   [ERROR] Failed on {name}: {exc}")
      finally:
        close_messenger_chat(page)
        print("   [Chat] Closed.")

      if idx < len(batch):
        wait = random.uniform(args.min_delay, args.max_delay)
        print(f"   Waiting {wait:.1f}s before next page...")
        time.sleep(wait)

    print(f"\n[Done] Sent {sent_count} messages.")
    context.close()


if __name__ == "__main__":
  main()

