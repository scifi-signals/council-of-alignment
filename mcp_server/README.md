# Council of Alignment — MCP Server

MCP server for [Council of Alignment](https://council.stardreamgames.com), a multi-model AI design review tool.

## Setup

Add to your Claude Code config (`~/.claude/settings.json`):

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

Get your API key from your [Council settings page](https://council.stardreamgames.com/settings).

## Tools

- `council_create_session` — Create a new review session
- `council_add_files` — Attach source files for review
- `council_send_message` — Chat with the Lead AI
- `council_convene` — Run the full 4-model review (3-5 min)
- `council_get_results` — Retrieve review results
- `council_decide` — Accept/reject proposed changes
- `council_list_sessions` — List your sessions

## Usage

From Claude Code, just say: "Have the Council review this project for security issues."

The AI agent handles creating the session, attaching files, framing the review, and convening the Council.
