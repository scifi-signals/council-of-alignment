"""GitHub auto-context — fetch repo trees and select files for reviewers."""

import re
import json
import asyncio
import base64
import logging
import httpx
from config import GITHUB_TOKEN

logger = logging.getLogger(__name__)

# Reuse the same allowed extensions as the upload handler
ALLOWED_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".txt", ".html", ".css",
    ".yaml", ".yml", ".toml", ".csv", ".xml", ".cfg", ".ini", ".sh", ".sql",
    ".env.example", ".gitignore", ".dockerfile", ".rst", ".r", ".go", ".rs",
}

SKIP_DIRS = {"__pycache__", "node_modules", ".git", "venv", ".venv", ".tox",
             ".mypy_cache", "dist", "build", ".next", ".nuxt", "coverage",
             ".pytest_cache", "eggs", "*.egg-info"}

_REPO_PATTERN = re.compile(r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/?#]+)")

FILE_SELECT_PROMPT = """You are selecting files for a code review. You have:
1. A conversation where a user and an AI designed a solution
2. The complete file tree of the codebase

Your job: select every file reviewers need to VALIDATE this design end-to-end.
Reviewers will trace data flows through the code. A missing file means a broken trace,
which means they'll report a false positive (claiming something is broken when it isn't).

RULE #1 — PREFER INCLUSION OVER SELECTION:
Count the source code files (.py, .js, .ts, .go, .rs, etc.) in the tree. If the total
is LESS than {max_files}, include ALL of them plus the main config files. Do not try to
be clever about which source files matter — a reviewer tracing data flows needs to see
every module. Only start making hard choices when source files exceed your budget.

RULE #2 — WHEN YOU MUST SELECT, INCLUDE AGGRESSIVELY:
- Every file directly mentioned or modified in the conversation
- Every file that IMPORTS FROM or IS IMPORTED BY a modified file
- Every file that READS data written by a modified file
- Every file that WRITES data read by a modified file
- Config files, schema definitions, and shared utilities
- The main entry point / orchestrator
- Files with names like: learner, learning, engine, core, main, agent, scheduler,
  worker, pipeline, processor — these contain critical logic

DO NOT INCLUDE spec files, design docs, or roadmaps (files ending in -spec.md, -CLAUDE.md,
-design.md, or similar). These describe what SHOULD exist, not what DOES exist. Including them
causes reviewers to diff specs against code and report unbuilt features as bugs. Only include
executable code files and config files that the code actually reads.

Think of it this way: if a reviewer asks "but where does X get consumed?" the answer
should be in the files you selected. When in doubt, include the file.

Here is the file tree:
```
{tree}
```

Here is the conversation:
{conversation}

Return ONLY a JSON array of file paths from the tree above, ranked by importance. Maximum {max_files} files.
Example: ["src/main.py", "src/utils.py", "config.yaml"]

Return the JSON array and nothing else."""


def parse_repo_url(url: str) -> dict | None:
    """Extract owner and repo from a GitHub URL. Returns None if invalid."""
    url = url.strip().rstrip("/")
    m = _REPO_PATTERN.search(url)
    if not m:
        return None
    repo = m.group("repo")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return {"owner": m.group("owner"), "repo": repo}


def _headers() -> dict:
    """Build GitHub API headers."""
    h = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


def _should_include(path: str) -> bool:
    """Check if a file path should be included based on extension and directory."""
    parts = path.replace("\\", "/").split("/")
    # Skip files inside ignored directories
    for part in parts:
        if part in SKIP_DIRS or part.endswith(".egg-info"):
            return False
    # Check extension
    fname = parts[-1]
    if "." not in fname:
        return False
    ext = "." + fname.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_EXT


class GitHubContextProvider:
    """Fetches repo structure and selects relevant files for council reviews."""

    async def fetch_repo_tree(self, owner: str, repo: str, branch: str = "main") -> tuple[list[str], str]:
        """Fetch the full file tree from GitHub. Returns (list of file paths, branch used)."""
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 404:
                # Try 'master' as fallback
                if branch == "main":
                    return await self.fetch_repo_tree(owner, repo, "master")
                raise ValueError(f"Repository {owner}/{repo} not found or branch '{branch}' doesn't exist.")
            if resp.status_code == 403:
                raise ValueError("GitHub API rate limit exceeded. Set GITHUB_TOKEN in .env for higher limits.")
            resp.raise_for_status()
            data = resp.json()

        tree = data.get("tree", [])
        # Filter to files only (not directories), apply extension filter
        paths = [
            item["path"] for item in tree
            if item.get("type") == "blob" and _should_include(item["path"])
        ]
        return paths, branch

    async def select_relevant_files(
        self, dispatcher, lead_model: str, conversation: str, tree: list[str], max_files: int = 15
    ) -> list[str]:
        """Use the Lead AI to select which files reviewers need to see."""
        tree_text = "\n".join(tree)
        prompt = FILE_SELECT_PROMPT.format(
            tree=tree_text, conversation=conversation, max_files=max_files
        )

        try:
            result = await dispatcher.chat(
                lead_model,
                [{"role": "user", "content": prompt}],
                system="You are a code review assistant. Return only valid JSON arrays of file paths."
            )
            content = result["content"].strip()

            # Extract JSON array from response (handle markdown code blocks)
            if "```" in content:
                # Pull content from inside code block
                match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
                if match:
                    content = match.group(1).strip()

            selected = json.loads(content)
            if not isinstance(selected, list):
                logger.warning("File selection returned non-list: %s", type(selected))
                return []

            # Validate against actual tree — drop hallucinated paths
            tree_set = set(tree)
            validated = [p for p in selected if p in tree_set]

            if len(validated) < len(selected):
                dropped = len(selected) - len(validated)
                logger.info("Dropped %d hallucinated file paths from selection", dropped)

            return validated[:max_files]

        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse file selection response: %s", e)
            return []
        except Exception as e:
            logger.error("File selection failed: %s", e)
            return []

    async def fetch_file_contents(
        self, owner: str, repo: str, file_paths: list[str], branch: str = "main"
    ) -> list[dict]:
        """Fetch file contents from GitHub in parallel. Returns list of attachment-style dicts."""
        if not file_paths:
            return []

        async def _fetch_one(path: str) -> dict | None:
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(url, headers=_headers())
                    if resp.status_code != 200:
                        logger.warning("Failed to fetch %s: %d", path, resp.status_code)
                        return None
                    data = resp.json()

                content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
                size = data.get("size", len(content))

                return {
                    "filename": path,
                    "content": content,
                    "size_bytes": size,
                }
            except Exception as e:
                logger.warning("Error fetching %s: %s", path, e)
                return None

        results = await asyncio.gather(*[_fetch_one(p) for p in file_paths])
        return [r for r in results if r is not None]
