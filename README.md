# Council of Alignment

**Design with one AI. Get critical review from three others. Ship with confidence.**

Council of Alignment is a multi-model design review tool. You chat with a Lead AI to develop your idea, then convene a council of three independent AI reviewers (Claude, ChatGPT, Gemini, Grok) for structured critical analysis. The synthesis preserves disagreement — you see where models agree, where they split, and where a lone voice raises a warning everyone else missed.

**Live at [council.stardreamgames.com](https://council.stardreamgames.com)**

## How It Works

1. **Chat with your Lead** — Pick any model as Lead AI. Brainstorm, refine requirements, attach source code or link a GitHub repo.
2. **Convene the Council** — One click sends everything to 3 independent reviewers. Parallel analysis, no groupthink.
3. **Review & Decide** — Structured synthesis with confidence levels: Accord (all agree), Majority (2 of 3), Dissent (split), Lone Warnings (one reviewer sees something the others missed). Accept or reject each proposed change individually.

Decisions feed into the next round. The Council tracks what changed, what was rejected, and why.

## Features

- **Structured disagreement** — Synthesis preserves dissent instead of averaging it away
- **Actionable proposals** — Concrete changes with confidence levels and source attribution
- **Multi-round evolution** — Each round builds on previous decisions
- **Full source context** — Zip files, GitHub repos, full codebases. Every reviewer sees everything
- **MCP server** — Use the Council from Claude Code, Cursor, or any MCP-compatible tool
- **REST API** — Programmatic access for CI/CD integration
- **BYOK** — Bring your own OpenRouter key for unlimited reviews. Keys encrypted at rest.

## Pricing

- **Free tier**: 1 council review to try it out. No credit card required.
- **BYOK**: Add your [OpenRouter API key](https://openrouter.ai/keys) for unlimited reviews, billed to your own account.

Council of Alignment is open source and free to self-host.

## MCP Integration

Add the Council to Claude Code or any MCP client:

```json
{
  "mcpServers": {
    "council-of-alignment": {
      "command": "uvx",
      "args": ["council-of-alignment"],
      "env": {
        "COUNCIL_API_KEY": "your-api-key"
      }
    }
  }
}
```

Get your API key from [Settings](https://council.stardreamgames.com/settings) after signing in.

Then from your IDE, just say: "Have the Council review this project for security issues." The AI agent handles creating the session, attaching files, and convening the Council.

## Self-Hosting

Requirements: Python 3.11+, an OpenRouter API key (or direct API keys for individual providers).

```bash
git clone https://github.com/scifi-signals/council-of-alignment.git
cd council-of-alignment
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Required environment variables
export OPENROUTER_API_KEY="your-key"
export ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
export GITHUB_CLIENT_ID="your-oauth-app-id"
export GITHUB_CLIENT_SECRET="your-oauth-app-secret"

uvicorn app:app --host 0.0.0.0 --port 8890
```

GitHub OAuth is required for authentication. [Create an OAuth App](https://github.com/settings/developers) with callback URL `http://localhost:8890/auth/callback`.

## Architecture

- **Backend**: Python, FastAPI, SQLite
- **Frontend**: Server-rendered Jinja2 templates, vanilla JS
- **AI routing**: OpenRouter (all 4 models via one API) or direct provider APIs
- **Auth**: GitHub OAuth
- **Key storage**: Fernet symmetric encryption (AES-128-CBC)

The council review pipeline: Lead AI gap scan → 3 parallel reviewer calls → structured synthesis → proposed changes with confidence levels.

## License

MIT
