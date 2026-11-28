#!/usr/bin/env python3
"""
Facebook Page outreach script (`<country>_facebook_leads.csv`).
1. Extracts country name from CSV filename (e.g., `spain`).
2. Uses `COUNTRY_LANGUAGE_MAP` / Qwen (`qwen2.5:7b` by default) ONCE at startup
   to resolve the country's spoken language and generate the outreach message
   template.
3. Automatically sets the default limit to the number of lines in the CSV
   (unless `--limit N` is passed).
4. Checks `sent_facebook.log` before each page, skips already-logged URLs,
   sends the message, closes the chat, logs to `sent_facebook.log`, and
   prints a final summary.
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

COUNTRY_LANGUAGE_MAP = {
    "ireland": "English",
    "united kingdom": "English",
    "uk": "English",
    "england": "English",
    "scotland": "English",
    "wales": "English",
    "united states": "English",
    "usa": "English",
    "canada": "English",
    "australia": "English",
    "new zealand": "English",
    "spain": "Spanish",
    "españa": "Spanish",
    "italy": "Italian",
    "italia": "Italian",
    "france": "French",
    "germany": "German",
    "portugal": "Portuguese",
    "morocco": "French",
    "maroc": "French",
    "belgium": "French",
    "switzerland": "German",
    "netherlands": "Dutch",
}

ENGLISH_FB_TEMPLATE = (
    "Hi {BUSINESS_NAME}! I found your business on Google Maps and noticed you "
    "don't seem to have a website. I'm a web developer, and I think your "
    "business could benefit from a simple website where customers can discover "
    "your services, photos, opening hours and contact information. I'd be "
    "happy to create a personalized website demo for your business, completely "
    "free, with no obligation. Would you be interested in seeing it? Best "
    "regards! bourais business"
)

# Distinct ANSI colors per step
RESET = "\033[0m"
CYAN = "\033[96m"      # Country, language detection & next page
MAGENTA = "\033[95m"   # Ollama message generation
YELLOW = "\033[93m"    # Generated template & log check
GREEN = "\033[92m"     # Sending, chat close & saved confirmation
DIM = "\033[90m"       # Skipped page
RED = "\033[91m"       # Errors & account block
BLUE = "\033[94m"      # Final summary


def colorize(text: str, color: str) -> str:
  return f"{color}{text}{RESET}"


def extract_country_from_filename(csv_path: Path) -> str:
  """Extract country name from filenames like `spain_facebook_leads.csv`."""
  stem = csv_path.stem.lower()
  cleaned = re.sub(
      r"(_facebook_leads|_fb_leads|_facebook|_leads|_fb)$", "", stem
  )
  country = cleaned.replace("_", " ").strip()
  if not country:
    sys.exit(
        colorize(
            f"Error: Could not extract country from '{csv_path.name}'. "
            "Name the file like '<country>_facebook_leads.csv'.",
            RED,
        )
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


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str,
    ollama_url: str,
    num_predict: int = 220,
    temperature: float = 0.1,
) -> str:
  payload = {
      "model": model,
      "stream": False,
      "messages": [
          {"role": "system", "content": system_prompt},
          {"role": "user", "content": user_prompt},
      ],
      "options": {
          "temperature": temperature,
          "num_predict": num_predict,
      },
  }
  resp = requests.post(ollama_url, json=payload, timeout=150)
  resp.raise_for_status()
  content = (resp.json().get("message") or {}).get("content", "")
  return clean_qwen_response(content)


def detect_business_language(country: str, model: str, ollama_url: str) -> str:
  mapped = COUNTRY_LANGUAGE_MAP.get(country.strip().lower())
  if mapped:
    return mapped

  try:
    lang_raw = call_ollama(
        system_prompt=(
            "Output ONLY the primary language used for business communication "
            "in the given country, in ONE word (e.g., English, Spanish, "
            "Italian, French). No extra text."
        ),
        user_prompt=f"Country: {country}",
        model=model,
        ollama_url=ollama_url,
        num_predict=15,
        temperature=0.0,
    )
    words = re.findall(r"[A-Za-zÀ-ÿ-]+", lang_raw)
    return words[0].capitalize() if words else "English"
  except Exception:
    return "English"


def detect_language_and_generate_template(
    country: str,
    model: str,
    ollama_url: str,
) -> tuple[str, str]:
  """
  Called ONCE at startup: resolves the main business language for `country`.
  Uses direct English when appropriate, otherwise translates via Ollama.
  """
  language = detect_business_language(country, model, ollama_url)
  print(
      colorize(
          f"country : {country} | language : {language} | model : {model}",
          CYAN,
      )
  )

  if language.lower() == "english":
    return language, ENGLISH_FB_TEMPLATE

  print(
      colorize(
          f"ollama generate message in {language} for ({country})...",
          MAGENTA,
      )
  )

  prompt = (
      f"Translate the message below accurately into {language}.\n"
      "Keep {BUSINESS_NAME} unchanged.\n"
      "Always end with: bourais business\n"
      "Keep it on a single line.\n"
      "Do not add extra sentences, commentary, or explanations.\n\n"
      f"ORIGINAL:\n{ENGLISH_FB_TEMPLATE}"
  )

  try:
    cleaned = call_ollama(
        system_prompt=(
            f"You write short outreach messages strictly in {language}. "
            "Output ONLY a single-line message with no quotes, notes, or "
            "explanations."
        ),
        user_prompt=prompt,
        model=model,
        ollama_url=ollama_url,
        num_predict=220,
        temperature=0.1,
    )
  except Exception as exc:
    print(colorize(f"Ollama error ({exc}) -> using English template", RED))
    return language, ENGLISH_FB_TEMPLATE

  if not cleaned or len(cleaned) < 60:
    print(
        colorize("Invalid/short translation -> using English template", YELLOW)
    )
    return language, ENGLISH_FB_TEMPLATE

  if "{BUSINESS_NAME}" not in cleaned:
    cleaned = cleaned.replace("{business_name}", "{BUSINESS_NAME}")

  if not cleaned.lower().endswith("bourais business"):
    cleaned = f"{cleaned.rstrip()} bourais business"

  return language, cleaned


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


def resolve_log_paths(
    log_arg: str, script_dir: Path, csv_dir: Path
) -> tuple[Path, list[Path]]:
  p = Path(log_arg)
  if p.is_absolute():
    return p.resolve(), [p.resolve()]

  candidates: list[Path] = []
  for base in (script_dir, Path.cwd(), csv_dir):
    resolved = (base / p).resolve()
    if resolved not in candidates:
      candidates.append(resolved)

  existing = [c for c in candidates if c.exists()]
  primary = existing[0] if existing else (script_dir / p).resolve()
  return primary, candidates


def load_sent_log(log_paths: list[Path]) -> set[str]:
  sent: set[str] = set()
  for lp in log_paths:
    if not lp.exists():
      continue
    for line in lp.read_text(encoding="utf-8", errors="ignore").splitlines():
      entry = line.strip()
      if entry:
        sent.add(entry.casefold())
        norm = normalize_fb_url(entry)
        if norm:
          sent.add(norm.casefold())
  return sent


def save_sent_log(log_path: Path, sent_set: set[str], fb_url: str) -> None:
  norm = normalize_fb_url(fb_url) or fb_url.strip().casefold()
  if not norm:
    return
  sent_set.add(norm)
  sent_set.add(fb_url.strip().casefold())
  log_path.parent.mkdir(parents=True, exist_ok=True)
  with open(log_path, mode="a", encoding="utf-8") as f:
    f.write(norm + "\n")
    f.flush()


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


def load_leads_from_csv(csv_path: Path) -> tuple[list[tuple[str, str]], int]:
  """Load all valid (business_name, normalized_fb_url) rows."""
  leads: list[tuple[str, str]] = []
  seen: set[str] = set()

  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    reader = csv.reader(f, skipinitialspace=True)
    rows = [r for r in reader if any(cell.strip() for cell in r)]

  if not rows:
    return leads, 0

  first_lower = [c.strip().lower() for c in rows[0]]
  has_header = any(
      h in ("facebook", "fb", "url", "link", "business name", "name", "business")
      for h in first_lower
  )

  if has_header:
    hmap = {h: idx for idx, h in enumerate(first_lower)}
    name_idx = next(
        (hmap[k] for k in ("business name", "name", "business") if k in hmap),
        0,
    )
    fb_idx = next(
        (hmap[k] for k in ("facebook", "fb", "url", "link") if k in hmap),
        1 if len(rows[0]) > 1 else 0,
    )
    data_rows = rows[1:]
  else:
    name_idx = 0
    fb_idx = 1 if len(rows[0]) > 1 else 0
    data_rows = rows

  for r in data_rows:
    raw_name = r[name_idx].strip() if name_idx < len(r) else ""
    raw_fb = r[fb_idx].strip() if fb_idx < len(r) else ""
    if "facebook.com" not in raw_fb.lower():
      for cell in r:
        if "facebook.com" in cell.lower():
          raw_fb = cell.strip()
          break
    norm_url = normalize_fb_url(raw_fb)
    if not norm_url or not is_valid_page_url(norm_url):
      continue
    if norm_url in seen:
      continue
    seen.add(norm_url)
    leads.append((raw_name or "there", norm_url))

  return leads, len(data_rows)


def count_csv_lines(csv_path: Path) -> int:
  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    reader = csv.reader(f, skipinitialspace=True)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
  if not rows:
    return 0
  first_lower = [c.strip().lower() for c in rows[0]]
  has_header = any(
      h in ("facebook", "fb", "url", "link", "business name", "name", "business")
      for h in first_lower
  )
  return len(rows[1:] if has_header else rows)


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
  chat_box.click(timeout=8000)
  time.sleep(0.5)
  try:
    chat_box.press_sequentially(
        message_text, delay=random.randint(5, 15), timeout=60000
    )
  except Exception:
    chat_box.click(timeout=5000)
    page.keyboard.insert_text(message_text)
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
  script_dir = Path(__file__).resolve().parent

  parser = argparse.ArgumentParser(
      description=(
          "Send Facebook Page messages using country name from CSV filename "
          "and Qwen once at startup."
      )
  )
  parser.add_argument("csv_file", help="Path to <country>_facebook_leads.csv")
  parser.add_argument(
      "--model",
      default="qwen2.5:7b",
      help="Local Ollama model name (default: qwen2.5:7b)",
  )
  parser.add_argument(
      "--ollama-url",
      default=OLLAMA_CHAT_URL,
      help=f"Ollama chat API URL (default: {OLLAMA_CHAT_URL})",
  )
  parser.add_argument(
      "--log-file",
      default="sent_facebook.log",
      help="Log file for sent Facebook URLs (default: sent_facebook.log)",
  )
  parser.add_argument(
      "--limit",
      type=int,
      default=0,
      help="Max NEW messages to send (0 = auto-set to number of lines in CSV)",
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

  csv_path = Path(args.csv_file).resolve()
  if not csv_path.exists():
    sys.exit(
        colorize(f"Error: File not found: {csv_path}", RED)
    )

  primary_log, all_logs = resolve_log_paths(
      args.log_file, script_dir, csv_path.parent
  )
  sent_entries = load_sent_log(all_logs)

  country = extract_country_from_filename(csv_path)
  _, message_template = detect_language_and_generate_template(
      country=country,
      model=args.model,
      ollama_url=args.ollama_url,
  )
  print(colorize(f"message template : {message_template}", YELLOW))

  all_leads, raw_rows = load_leads_from_csv(csv_path)
  total_leads = len(all_leads)
  csv_line_count = count_csv_lines(csv_path)

  effective_limit = (
      min(args.limit, 40) if args.limit > 0 else min(csv_line_count, 40)
  )

  filtered_out = max(0, raw_rows - total_leads)
  print(
      colorize(
          f"get {total_leads} pages from {csv_path.name} "
          f"(limit set to {effective_limit}; {raw_rows} total rows, "
          f"{filtered_out} non-page/duplicate skipped)",
          CYAN,
      )
  )

  if not all_leads or effective_limit <= 0:
    print(colorize("Summary -> sent: 0 | not sent: 0", BLUE))
    return

  launch_kwargs = {
      "user_data_dir": args.profile_dir,
      "headless": False,
      "viewport": {"width": 1280, "height": 850},
  }
  if args.use_chrome:
    launch_kwargs["channel"] = "chrome"

  sent_count = 0
  not_sent_count = 0

  with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(**launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
    time.sleep(4)

    if not is_logged_in(page):
      print(
          colorize(
              "\n[Login] Please log into Facebook in Chrome. Waiting up to 3"
              " minutes...",
              YELLOW,
          )
      )
      page.wait_for_selector(
          "input[name='email']", state="detached", timeout=180000
      )
      time.sleep(3)

    close_messenger_chat(page)

    for idx, (name, fb_url) in enumerate(all_leads, 1):
      if sent_count >= effective_limit:
        break

      print(
          colorize(
              f"[{idx}/{total_leads}] check if {fb_url} is in {primary_log.name}",
              YELLOW,
          )
      )
      if (
          fb_url.casefold() in sent_entries
          or fb_url.strip().casefold() in sent_entries
      ):
        not_sent_count += 1
        print(
            colorize(
                f"skipped (already in {primary_log.name}): {fb_url}", DIM
            )
        )
        continue

      close_messenger_chat(page)
      message_text = message_template.replace("{BUSINESS_NAME}", name)
      print(colorize(f"opening {name} : {fb_url}", CYAN))

      try:
        page.goto(fb_url, wait_until="domcontentloaded", timeout=30000)
      except Exception as exc:
        not_sent_count += 1
        print(colorize(f"not sent ({name}): {exc}", RED))
        continue

      time.sleep(random.uniform(4.0, 6.5))

      blocked, reason = has_account_block(page)
      if blocked:
        print(colorize(f"\n[STOP] Stopped: {reason}", RED))
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
        not_sent_count += 1
        save_sent_log(primary_log, sent_entries, fb_url)
        print(
            colorize(
                f"skipped (no Message button, logged to {primary_log.name}): {fb_url}",
                DIM,
            )
        )
        continue

      try:
        msg_btn.click(timeout=6000)
        time.sleep(random.uniform(3.0, 5.0))
      except Exception as exc:
        not_sent_count += 1
        print(colorize(f"not sent ({name}): {exc}", RED))
        continue

      blocked, reason = has_account_block(page)
      if blocked:
        print(colorize(f"\n[STOP] Stopped after opening chat: {reason}", RED))
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
        not_sent_count += 1
        print(colorize(f"not sent ({name}): chat box not found", RED))
        close_messenger_chat(page)
        continue

      try:
        print(colorize(f"sending message to {name} ({fb_url})", GREEN))
        if args.dry_run:
          chat_box.click(timeout=8000)
          time.sleep(0.5)
          try:
            chat_box.press_sequentially(
                message_text, delay=random.randint(5, 15), timeout=60000
            )
          except Exception:
            chat_box.click(timeout=5000)
            page.keyboard.insert_text(message_text)

          sent_count += 1
          print(colorize(f"[dry-run] typed message for {name}", GREEN))
        else:
          send_message_and_verify(page, chat_box, message_text)
          save_sent_log(primary_log, sent_entries, fb_url)
          sent_count += 1
          print(
              colorize(
                  f"sent and saved to {primary_log.name}: {fb_url}", GREEN
              )
          )
      except Exception as exc:
        not_sent_count += 1
        print(colorize(f"not sent ({name}): {exc}", RED))
      finally:
        close_messenger_chat(page)
        print(colorize("closed chat", GREEN))

      if idx < total_leads and sent_count < effective_limit:
        wait = random.uniform(args.min_delay, args.max_delay)
        print(
            colorize(
                f"next page ({idx + 1}/{total_leads}) in {wait:.1f}s",
                CYAN,
            )
        )
        time.sleep(wait)

    context.close()

  print(
      colorize(
          f"\nSummary -> sent: {sent_count} | not sent: {not_sent_count}",
          BLUE,
      )
  )


if __name__ == "__main__":
  main()
