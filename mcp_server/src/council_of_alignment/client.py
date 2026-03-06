"""HTTP client for Council of Alignment API v1."""

import os
import httpx


class CouncilClient:
    """Async wrapper around the Council API v1 endpoints."""

    def __init__(self):
        self.base_url = os.environ.get("COUNCIL_API_URL", "https://council.stardreamgames.com")
        self.api_key = os.environ.get("COUNCIL_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("COUNCIL_API_KEY environment variable is required")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def health(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.base_url}/api/v1/health")
            r.raise_for_status()
            return r.json()

    async def create_session(self, title: str, lead_model: str = "claude") -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self.base_url}/api/v1/sessions",
                headers=self._headers(),
                json={"title": title, "lead_model": lead_model},
            )
            r.raise_for_status()
            return r.json()

    async def list_sessions(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{self.base_url}/api/v1/sessions",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def add_files(self, session_id: str, files: list[dict]) -> dict:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/api/v1/sessions/{session_id}/files",
                headers=self._headers(),
                json={"files": files},
            )
            r.raise_for_status()
            return r.json()

    async def send_message(self, session_id: str, message: str) -> dict:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/api/v1/sessions/{session_id}/message",
                headers=self._headers(),
                json={"message": message},
            )
            r.raise_for_status()
            return r.json()

    async def convene(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(
                f"{self.base_url}/api/v1/sessions/{session_id}/convene",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def get_results(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{self.base_url}/api/v1/sessions/{session_id}/results",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def decide(self, session_id: str, decisions: list[dict]) -> dict:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/api/v1/sessions/{session_id}/decide",
                headers=self._headers(),
                json={"decisions": decisions},
            )
            r.raise_for_status()
            return r.json()
