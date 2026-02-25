"""Find correct OpenRouter model IDs."""
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

key = os.getenv("OPENROUTER_API_KEY")

# Get model list from OpenRouter
r = httpx.get("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
models = r.json().get("data", [])

# Search for our models
for search in ["claude", "gpt-4o", "gemini-2", "grok"]:
    print(f"\n--- {search} ---")
    matches = [m for m in models if search.lower() in m["id"].lower()]
    for m in sorted(matches, key=lambda x: x["id"])[:8]:
        print(f"  {m['id']}")
