import os, requests
from dotenv import load_dotenv
load_dotenv()
key = os.getenv("SERPER_API_KEY", "").strip()
print("Key len:", len(key), "repr:", repr(key[:8]) + "..." + repr(key[-4:]) if len(key) > 12 else repr(key))
r = requests.post(
    "https://google.serper.dev/search",
    headers={"X-API-KEY": key, "Content-Type": "application/json"},
    json={"q": "Souvenir Alegria Sevilla instagram", "gl": "es", "hl": "es", "num": 5},
    timeout=20,
)
print("Status:", r.status_code)
print("Body:", r.text)
