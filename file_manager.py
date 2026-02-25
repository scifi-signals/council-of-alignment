"""File manager — generates exportable design documents."""

import os
import json
from datetime import datetime
from config import MODELS


class FileManager:
    async def generate_design_doc(self, session: dict, latest_version: dict) -> tuple[str, str]:
        """Generate the main design document as markdown."""
        title = session["title"]
        lead = MODELS.get(session["lead_model"], {}).get("name", session["lead_model"])
        content = latest_version["content"] if latest_version else "No design versions saved yet."
        version_num = latest_version["version_number"] if latest_version else 0

        doc = f"""# {title}

**Version**: {version_num}
**Lead AI**: {lead}
**Generated**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

---

{content}
"""
        filename = f"{_slugify(title)}-design-v{version_num}.md"
        return filename, doc

    async def generate_changelog(self, session: dict, changelog: list[dict]) -> tuple[str, str]:
        """Generate a formatted changelog with attribution."""
        title = session["title"]

        lines = [f"# {title} — Changelog\n"]
        lines.append(f"**Generated**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")

        if not changelog:
            lines.append("No changes recorded yet.\n")
        else:
            lines.append("| Round | Category | Change | Source | Confidence | Status |")
            lines.append("|-------|----------|--------|--------|------------|--------|")
            for c in changelog:
                reviewers = ", ".join(
                    MODELS.get(r, {}).get("name", r) for r in c.get("source_reviewers", [])
                )
                status = "Accepted" if c.get("accepted") else "Rejected"
                if c.get("rejection_reason"):
                    status += f" ({c['rejection_reason']})"
                lines.append(
                    f"| {c.get('round_number', '?')} "
                    f"| {c.get('category', 'other')} "
                    f"| {c['description']} "
                    f"| {reviewers} "
                    f"| {c.get('confidence', '?')} "
                    f"| {status} |"
                )

        filename = f"{_slugify(title)}-changelog.md"
        return filename, "\n".join(lines)

    async def generate_all_files(self, session: dict, latest_version: dict, changelog: list[dict]) -> list[tuple[str, str]]:
        """Generate all exportable files."""
        files = []
        files.append(await self.generate_design_doc(session, latest_version))
        files.append(await self.generate_changelog(session, changelog))
        return files

    def write_files(self, files: list[tuple[str, str]], output_dir: str):
        """Write files to disk."""
        os.makedirs(output_dir, exist_ok=True)
        written = []
        for filename, content in files:
            path = os.path.join(output_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(path)
        return written


def _slugify(text: str) -> str:
    """Simple slugify for filenames."""
    return "".join(c if c.isalnum() or c in "-_ " else "" for c in text).strip().replace(" ", "-").lower()
