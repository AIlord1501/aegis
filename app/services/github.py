from typing import Any

import httpx

from app.core.config import get_settings


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.base_url = get_settings().github_api_base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=get_settings().llm_timeout_seconds) as client:
            response = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params)
        if response.is_error:
            raise GitHubError(f"GitHub request failed ({response.status_code}): {response.text[:300]}")
        return response.json()

    async def repository(self, full_name: str) -> dict[str, Any]:
        return await self._get(f"/repos/{full_name}")

    async def commits(self, full_name: str, branch: str, per_page: int = 20) -> list[dict[str, Any]]:
        return await self._get(f"/repos/{full_name}/commits", {"sha": branch, "per_page": per_page})

    async def pull_requests(self, full_name: str, per_page: int = 20) -> list[dict[str, Any]]:
        return await self._get(f"/repos/{full_name}/pulls", {"state": "all", "per_page": per_page})

    async def code_search(self, full_name: str, query: str, per_page: int = 20) -> list[dict[str, Any]]:
        data = await self._get("/search/code", {"q": f"{query} repo:{full_name}", "per_page": per_page})
        return data.get("items", [])

    async def tree(self, full_name: str, branch: str) -> list[dict[str, Any]]:
        data = await self._get(f"/repos/{full_name}/git/trees/{branch}", {"recursive": "1"})
        return [item for item in data.get("tree", []) if item.get("type") == "blob"]

    async def file(self, full_name: str, path: str, ref: str) -> tuple[str, dict[str, Any]]:
        data = await self._get(f"/repos/{full_name}/contents/{path}", {"ref": ref})
        if data.get("encoding") != "base64":
            raise GitHubError(f"Unsupported GitHub content encoding for {path}")
        import base64

        return base64.b64decode(data["content"]).decode("utf-8", errors="replace"), data
