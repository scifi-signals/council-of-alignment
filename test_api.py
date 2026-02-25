"""Quick test: does OpenRouter work with our key and model IDs?"""
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

key = os.getenv("OPENROUTER_API_KEY")
print(f"Key: {key[:20]}..." if key else "NO KEY")

for model_id in [
    "anthropic/claude-sonnet-4-20250514",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-5-sonnet-20241022",
]:
    print(f"\nTesting {model_id}...")
    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model_id, "messages": [{"role": "user", "content": "say hi in 5 words"}]},
            timeout=30,
        )
        print(f"  Status: {r.status_code}")
        print(f"  Body: {r.text[:300]}")
        if r.status_code == 200:
            print("  SUCCESS")
            break
    except Exception as e:
        print(f"  Error: {e}")
