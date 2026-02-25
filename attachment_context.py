"""Build smart attachment context for AI prompts — file tree + prioritized content."""


# Priority tiers: lower number = included first
# Tier 1: Core source code (the logic models should review)
# Tier 2: Templates, docs, specs (important context)
# Tier 3: Config, data, styles (include if room)
_TIER_1 = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".r", ".rb", ".java"}
_TIER_2 = {".md", ".txt", ".rst", ".html", ".sql", ".sh"}
_TIER_3 = {".json", ".yaml", ".yml", ".toml", ".css", ".csv", ".xml", ".cfg", ".ini",
           ".env.example", ".gitignore", ".dockerfile"}

CHAR_BUDGET = 200_000       # ~50K tokens — leaves room for conversation + prompts
PER_FILE_CAP = 40_000       # no single file eats more than ~10K tokens


def _tier(filename: str) -> int:
    """Assign priority tier to a file. Lower = higher priority."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext in _TIER_1:
        return 1
    if ext in _TIER_2:
        return 2
    return 3


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    if b < 1024 * 1024:
        return f"{b / 1024:.0f}KB"
    return f"{b / (1024 * 1024):.1f}MB"


def build_attachment_context(attachments: list[dict], heading: str = "Reference Codebase",
                             priority_files: list[str] | None = None,
                             auto_selected: bool = False) -> str:
    """Build a context string from attachments with file tree + prioritized content.

    Each attachment dict must have 'filename', 'content', and optionally 'size_bytes'.
    priority_files: list of filenames (or partial names) to always include first,
                    regardless of tier. Used when the user mentions a specific file.
    auto_selected: if True, these are GitHub auto-selected files (tier 0.5 priority,
                   different heading style).
    Returns a markdown string ready to append to a system prompt or briefing.
    """
    if not attachments:
        return ""

    priority_files = priority_files or []

    # ── File tree (always included, costs almost nothing) ──
    tree_lines = []
    for att in sorted(attachments, key=lambda a: a["filename"]):
        size = _fmt_size(att.get("size_bytes", len(att["content"])))
        tree_lines.append(f"  {att['filename']}  ({size})")

    source_label = "(auto-selected from GitHub)" if auto_selected else "(uploaded by user)"
    section = f"\n\n## {heading} {source_label}\n\n"
    section += f"### Project Structure ({len(attachments)} files)\n```\n"
    section += "\n".join(tree_lines)
    section += "\n```\n"

    # ── Sort by priority: boosted files first (tier 0), then by normal tier ──
    # Auto-selected files all enter at tier 0.5 (above generic source, below user-boosted)
    def _sort_key(att):
        fname = att["filename"]
        for pf in priority_files:
            # Match on exact filename, basename, or substring
            if pf.lower() in fname.lower():
                return (0, fname)  # tier 0 = always first
        if auto_selected:
            return (0.5, fname)  # tier 0.5 for auto-selected GitHub files
        return (_tier(fname), fname)

    ranked = sorted(attachments, key=_sort_key)

    # ── Fill budget with full file contents ──
    used = 0
    included_files = []
    skipped_files = []

    for att in ranked:
        content = att["content"]
        # Cap individual files
        truncated = False
        if len(content) > PER_FILE_CAP:
            content = content[:PER_FILE_CAP]
            truncated = True

        if used + len(content) > CHAR_BUDGET:
            skipped_files.append(att["filename"])
            continue

        ext = att["filename"].rsplit(".", 1)[-1] if "." in att["filename"] else ""
        file_block = f"\n### {att['filename']}\n```{ext}\n{content}\n```\n"
        if truncated:
            file_block = f"\n### {att['filename']} *(truncated — {_fmt_size(att.get('size_bytes', 0))} total)*\n```{ext}\n{content}\n```\n"

        section += file_block
        used += len(content)
        included_files.append(att["filename"])

    # ── Note what was skipped ──
    if skipped_files:
        section += f"\n---\n*{len(skipped_files)} file{'s' if len(skipped_files) != 1 else ''} not shown "
        section += f"(context budget reached). Refer to the file tree above for the full project structure. "
        section += f"Skipped: {', '.join(skipped_files[:10])}"
        if len(skipped_files) > 10:
            section += f" and {len(skipped_files) - 10} more"
        section += ".*\n"

    return section
