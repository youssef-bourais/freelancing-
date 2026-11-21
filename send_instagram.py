#!/usr/bin/env python3
"""
Hands-free Instagram DM automation for `<country>_instagram_leads.csv`.
- Extracts country name from CSV filename (e.g., `spain`).
- Contacts Ollama (`qwen2.5:7b`) ONCE at startup to detect the spoken language
  and generate a single-line outreach message template with `{BUSINESS_NAME}`.
- Processes ALL rows in the CSV by default (unless `--limit N` is passed).
- Checks `sent_instagram.log`, skips already-sent accounts, clicks the
  profile's Message button, sends the DM, closes the chat view, logs to
  `sent_instagram.log`, and prints a final summary.
"""

import argparse
import csv
from pathlib import Path
import random
import re
import sys
import time
from urllib.parse import urlparse, urlunparse
from playwright.sync_api import sync_playwright
import requests

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"

# Distinct ANSI colors per step
RESET = "\033[0m"
CYAN = "\033[96m"      # Country, language detection & next account
MAGENTA = "\033[95m"   # Ollama message generation
YELLOW = "\033[93m"    # Generated message & log check
GREEN = "\033[92m"     # Sending, chat close & saved confirmation
DIM = "\033[90m"       # Skipped account
RED = "\033[91m"       # Errors & account block
BLUE = "\033[94m"      # Final summary


def colorize(text: str, color: str) -> str:
  return f"{color}{text}{RESET}"


def extract_country(csv_path: Path) -> str:
  stem = csv_path.stem.lower()
  cleaned = re.sub(
      r"(_instagram_leads|_ig_leads|_instagram|_ig|_leads)$",
      "",
      stem,
  )
  country = cleaned.replace("_", " ").strip()
  if not country:
    sys.exit(
        colorize(f"Error: Cannot detect country from '{csv_path.name}'.", RED)
    )
  return country


def clean_model_text(raw: str) -> str:
  if not raw:
    return ""
  text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
  if "</think>" in text:
    text = text.split("</think>")[-1].strip()
  text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
  lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
  msg = " ".join(lines).strip().strip('"').strip("'").strip()
  return re.sub(r"\s+", " ", msg).strip()


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str,
    ollama_url: str,
    num_predict: int = 160,
    temperature: float = 0.25,
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
  resp = requests.post(ollama_url, json=payload, timeout=180)
  resp.raise_for_status()
  content = (resp.json().get("message") or {}).get("content", "")
  return clean_model_text(content)


def detect_language_and_generate_dm(
    country: str,
    model: str,
    ollama_url: str,
) -> tuple[str, str]:
  """
  Runs ONCE at startup: detects spoken language for `country` and generates
  a single-line DM template containing `{BUSINESS_NAME}`.
  """
  try:
    lang_raw = call_ollama(
        system_prompt=(
            "Output ONLY the primary spoken language of the country in one or "
            "two words (e.g., Spanish, French, Arabic, English). No extra text."
        ),
        user_prompt=f"Country: {country}",
        model=model,
        ollama_url=ollama_url,
        num_predict=20,
        temperature=0.0,
    )
  except Exception as exc:
    sys.exit(colorize(f"Error: Ollama language detection failed: {exc}", RED))

  words = re.findall(r"[A-Za-zÀ-ÿ-]+", lang_raw)
  language = words[-1].lower() if words else "spanish"
  print(colorize(f"country : {country} | language : {language}", CYAN))

  print(
      colorize(
          f"ollama generate message in {language} for ({country})...",
          MAGENTA,
      )
  )
  try:
    template = call_ollama(
        system_prompt=(
            f"You write short cold outreach DMs in {language}. Output ONLY "
            "the message text on a single line with no quotes, notes, or "
            "explanations. Always keep {BUSINESS_NAME} as the placeholder."
        ),
        user_prompt=(
            f"Country: {country}\n"
            f"Spoken language: {language}\n"
            "Write a short, friendly Instagram DM offering to create a "
            "professional website for {BUSINESS_NAME}, found on Google Maps, "
            "to help get more customers, and ask if they want a quick example. "
            "Sign off as BOURAIS busine."
        ),
        model=model,
        ollama_url=ollama_url,
        num_predict=150,
        temperature=0.25,
    )
  except Exception as exc:
    sys.exit(colorize(f"Error: Ollama message generation failed: {exc}", RED))

  if not template:
    sys.exit(colorize("Error: Ollama returned an empty DM template.", RED))

  if "{BUSINESS_NAME}" not in template:
    template = template.replace("{business_name}", "{BUSINESS_NAME}")
    if "{BUSINESS_NAME}" not in template:
      template = f"{template} ({{BUSINESS_NAME}})"

  return language, template


def normalize_ig_url(raw_url: str) -> str:
  s = (raw_url or "").strip()
  if not s:
    return ""
  if s.startswith("@"):
    s = f"https://instagram.com/{s[1:]}"
  elif not s.startswith(("http://", "https://")):
    if "instagram.com" in s.lower():
      s = "https://" + s
    else:
      return ""
  parsed = urlparse(s)
  host = parsed.netloc.lower()
  if "instagram.com" not in host:
    return ""
  host = "instagram.com"
  path = parsed.path.rstrip("/")
  if not path:
    return ""
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


def load_sent_set(log_paths: list[Path]) -> set[str]:
  sent: set[str] = set()
  for lp in log_paths:
    if not lp.exists():
      continue
    for line in lp.read_text(encoding="utf-8", errors="ignore").splitlines():
      cleaned = line.strip()
      if not cleaned:
        continue
      sent.add(cleaned.casefold())
      norm = normalize_ig_url(cleaned)
      if norm:
        sent.add(norm.casefold())
  return sent


def save_sent_ig(log_path: Path, sent_set: set[str], ig_url: str) -> None:
  norm = normalize_ig_url(ig_url) or ig_url.strip().casefold()
  if not norm:
    return
  sent_set.add(norm)
  log_path.parent.mkdir(parents=True, exist_ok=True)
  with open(log_path, mode="a", encoding="utf-8") as f:
    f.write(norm + "\n")
    f.flush()


def is_valid_profile_url(url: str) -> bool:
  bad_segments = (
      "/p/",
      "/reel/",
      "/reels/",
      "/explore/",
      "/stories/",
      "/direct/",
      "/accounts/",
  )
  if any(seg in url.lower() for seg in bad_segments):
    return False
  path = urlparse(url).path.strip("/").lower()
  if path in ("", "popular", "directory", "about", "legal", "privacy", "terms"):
    return False
  return True


def load_leads_from_csv(csv_path: Path) -> list[tuple[str, str]]:
  """Load all valid (business_name, normalized_ig_url) rows with or without headers."""
  leads: list[tuple[str, str]] = []
  seen: set[str] = set()

  with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
    reader = csv.reader(f, skipinitialspace=True)
    rows = [r for r in reader if any(cell.strip() for cell in r)]

  if not rows:
    return leads

  first_lower = [c.strip().lower() for c in rows[0]]
  has_header = any(
      h in ("instagram", "ig", "url", "link", "business name", "name", "business")
      for h in first_lower
  )

  if has_header:
    hmap = {h: idx for idx, h in enumerate(first_lower)}
    name_idx = next(
        (hmap[k] for k in ("business name", "name", "business") if k in hmap),
        0,
    )
    ig_idx = next(
        (hmap[k] for k in ("instagram", "ig", "url", "link") if k in hmap),
        1 if len(rows[0]) > 1 else 0,
    )
    data_rows = rows[1:]
  else:
    name_idx = 0
    ig_idx = 1 if len(rows[0]) > 1 else 0
    data_rows = rows

  for r in data_rows:
    raw_name = r[name_idx].strip() if name_idx < len(r) else ""
    raw_ig = r[ig_idx].strip() if ig_idx < len(r) else ""
    if "instagram.com" not in raw_ig.lower():
      for cell in r:
        if "instagram.com" in cell.lower():
          raw_ig = cell.strip()
          break
    norm_url = normalize_ig_url(raw_ig)
    if not norm_url or not is_valid_profile_url(norm_url):
      continue
    if norm_url in seen:
      continue
    seen.add(norm_url)
    leads.append((raw_name or "there", norm_url))

  return leads


def is_logged_in(page) -> bool:
  return page.locator("input[name='username']").count() == 0


def has_block_or_challenge(page) -> bool:
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


def dismiss_notifications_popup(page) -> None:
  not_now_btn = page.locator(
      "button:has-text('Not Now'), button:has-text('Ahora no'),"
      " div[role='button']:has-text('Not Now'),"
      " div[role='button']:has-text('Ahora no')"
  ).first
  if not_now_btn.count() > 0:
    try:
      if not_now_btn.is_visible():
        not_now_btn.click(timeout=1500)
        time.sleep(0.5)
    except Exception:
      pass


def find_profile_message_button(page):
  """
  Find ONLY the 'Message' / 'Enviar mensaje' button inside the profile header
  (never the 'Messages' navigation link in the left sidebar).
  """
  exact_labels = re.compile(
      r"^(Message|Enviar mensaje|Mensaje|Envoyer un message|Contacter)$",
      flags=re.IGNORECASE,
  )

  for scope_sel in ("header", "main"):
    scope = page.locator(scope_sel)
    if scope.count() == 0:
      continue

    candidates = scope.locator("div[role='button'], button").filter(
        has_text=exact_labels
    )
    for i in range(candidates.count()):
      btn = candidates.nth(i)
      try:
        if not btn.is_visible():
          continue
        txt = (btn.inner_text() or "").strip()
        if not exact_labels.match(txt):
          continue
        in_nav = btn.evaluate("el => Boolean(el.closest('nav'))")
        href = btn.get_attribute("href") or ""
        if in_nav or "/direct/inbox" in href:
          continue
        return btn
      except Exception:
        continue

  return None


def find_dm_chat_box(page):
  """Locate Instagram's DM message input box across modal and thread views."""
  selectors = [
      "div[aria-label='Message'][role='textbox'][contenteditable='true']",
      "div[aria-label='Mensaje'][role='textbox'][contenteditable='true']",
      "div[role='textbox'][contenteditable='true']",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "textarea[placeholder*='Message'], textarea[placeholder*='Mensaje']",
  ]
  for sel in selectors:
    loc = page.locator(sel)
    for i in range(loc.count() - 1, -1, -1):
      item = loc.nth(i)
      try:
        if item.is_visible():
          return item
      except Exception:
        pass
  return None


def close_ig_chat(page) -> None:
  """Close open Instagram DM modal/thread or popups after sending."""
  dismiss_notifications_popup(page)

  close_selectors = [
      "svg[aria-label='Close']",
      "svg[aria-label='Cerrar']",
      "div[aria-label='Close'][role='button']",
      "div[aria-label='Cerrar'][role='button']",
      "button[aria-label='Close']",
      "button[aria-label='Cerrar']",
  ]
  close_loc = page.locator(", ".join(close_selectors))
  for _ in range(3):
    if close_loc.count() == 0:
      break
    clicked = False
    for i in range(close_loc.count() - 1, -1, -1):
      target = close_loc.nth(i)
      try:
        if target.is_visible():
          target.click(timeout=1500)
          time.sleep(0.5)
          clicked = True
          break
      except Exception:
        pass
    if not clicked:
      break

  chat_box = find_dm_chat_box(page)
  if chat_box is not None:
    try:
      chat_box.focus(timeout=1500)
      page.keyboard.press("Escape")
      time.sleep(0.5)
    except Exception:
      pass

  if "/direct/" in page.url.lower():
    try:
      page.go_back(wait_until="domcontentloaded", timeout=10000)
      time.sleep(1.0)
    except Exception:
      pass


def main() -> None:
  script_dir = Path(__file__).resolve().parent

  parser = argparse.ArgumentParser(
      description=(
          "Hands-free Instagram DM sender using Ollama (qwen2.5:7b) once at"
          " startup and `sent_instagram.log` deduplication."
      )
  )
  parser.add_argument("csv_file", help="Path to <country>_instagram_leads.csv")
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
      default="sent_instagram.log",
      help="Log file for sent Instagram URLs (default: sent_instagram.log)",
  )
  parser.add_argument(
      "--limit",
      type=int,
      default=0,
      help="Max NEW DMs to send in this run (0 = process all rows, default: 0)",
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
      help="Type the message without pressing Enter to send",
  )
  args = parser.parse_args()

  csv_path = Path(args.csv_file).resolve()
  if not csv_path.exists():
    sys.exit(colorize(f"Error: File not found: {csv_path}", RED))

  primary_log, all_logs = resolve_log_paths(
      args.log_file, script_dir, csv_path.parent
  )
  sent_set = load_sent_set(all_logs)

  country = extract_country(csv_path)
  _, dm_template = detect_language_and_generate_dm(
      country=country,
      model=args.model,
      ollama_url=args.ollama_url,
  )
  print(colorize(f"message template : {dm_template}", YELLOW))

  all_leads = load_leads_from_csv(csv_path)
  total_leads = len(all_leads)
  print(
      colorize(
          f"get {total_leads} accounts from {csv_path.name}",
          CYAN,
      )
  )

  if not all_leads:
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
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    time.sleep(4)

    if not is_logged_in(page):
      print(
          colorize(
              "\n[Login] Please log into Instagram in the browser window. "
              "Waiting up to 3 minutes...",
              YELLOW,
          )
      )
      page.wait_for_selector(
          "input[name='username']", state="detached", timeout=180000
      )
      time.sleep(3)

    dismiss_notifications_popup(page)

    for idx, (name, ig_url) in enumerate(all_leads, 1):
      if args.limit > 0 and sent_count >= args.limit:
        break

      print(
          colorize(
              f"[{idx}/{total_leads}] check if {ig_url} is in {primary_log.name}",
              YELLOW,
          )
      )
      if ig_url.casefold() in sent_set:
        not_sent_count += 1
        print(
            colorize(
                f"skipped (already in {primary_log.name}): {ig_url}", DIM
            )
        )
        continue

      dm_text = dm_template.replace("{BUSINESS_NAME}", name)
      print(colorize(f"opening {name} : {ig_url}", CYAN))
      try:
        page.goto(ig_url, wait_until="domcontentloaded", timeout=30000)
      except Exception as exc:
        not_sent_count += 1
        print(colorize(f"not sent ({name}): {exc}", RED))
        continue

      time.sleep(random.uniform(3.5, 6.0))

      if has_block_or_challenge(page):
        print(
            colorize(
                "\n[STOP] Instagram challenge or rate-limit detected.", RED
            )
        )
        break

      msg_btn = find_profile_message_button(page)

      if msg_btn is None:
        not_sent_count += 1
        save_sent_ig(primary_log, sent_set, ig_url)
        print(
            colorize(
                f"skipped (no profile Message button, logged): {ig_url}", DIM
            )
        )
        continue

      try:
        msg_btn.click(timeout=6000)
        time.sleep(random.uniform(3.5, 5.5))
      except Exception as exc:
        not_sent_count += 1
        print(colorize(f"not sent ({name}): {exc}", RED))
        continue

      dismiss_notifications_popup(page)

      chat_box = None
      for _ in range(8):
        chat_box = find_dm_chat_box(page)
        if chat_box is not None:
          break
        time.sleep(0.8)

      if chat_box is None:
        not_sent_count += 1
        print(colorize(f"not sent ({name}): chat box not found", RED))
        close_ig_chat(page)
        continue

      try:
        chat_box.click(timeout=5000)
        time.sleep(random.uniform(0.6, 1.2))
        chat_box.press_sequentially(
            dm_text, delay=random.randint(25, 55)
        )
        time.sleep(random.uniform(1.0, 2.0))

        if args.dry_run:
          sent_count += 1
          print(colorize(f"[dry-run] typed message for {name}", GREEN))
        else:
          page.keyboard.press("Enter")
          time.sleep(2.5)
          save_sent_ig(primary_log, sent_set, ig_url)
          sent_count += 1
          print(
              colorize(
                  f"sent and saved to {primary_log.name}: {ig_url}", GREEN
              )
          )
      except Exception as exc:
        not_sent_count += 1
        print(colorize(f"not sent ({name}): {exc}", RED))
      finally:
        close_ig_chat(page)
        print(colorize("closed chat", GREEN))

      if idx < total_leads and (args.limit == 0 or sent_count < args.limit):
        wait = random.uniform(args.min_delay, args.max_delay)
        print(
            colorize(
                f"next account ({idx + 1}/{total_leads}) in {wait:.1f}s",
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
