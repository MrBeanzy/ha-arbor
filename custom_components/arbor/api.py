"""Minimal Arbor guardian-portal client: log in and read account balances."""
from __future__ import annotations

import json
import logging
import re

_LOGGER = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class ArborAuthError(Exception):
    """Login rejected."""


class ArborError(Exception):
    """Any other failure."""


class ArborApi:
    """Logs in with the guardian's credentials and scrapes the dashboard JSON."""

    def __init__(self, session, base_url: str, username: str, password: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._user = username
        self._pass = password
        self._logged_in = False

    def _headers(self) -> dict:
        return {
            "User-Agent": _UA,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

    async def _login(self) -> None:
        # Seed cookies the way the SPA login page does (a browser-like GET).
        try:
            await self._session.get(
                self._base + "/",
                headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,*/*"},
            )
        except Exception:  # noqa: BLE001 - best effort to seed cookies
            pass
        # Arbor's login is a JSON POST: {"items":[{"username": <email>, "password": ...}]}
        body = {"items": [{"username": self._user, "password": self._pass}]}
        url = self._base + "/auth/login?lang=en"
        headers = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
        async with self._session.post(url, json=body, headers=headers) as resp:
            text = await resp.text()
        ok = False
        try:
            payload = json.loads(text)
            ok = bool(isinstance(payload, dict) and payload.get("success"))
        except ValueError:
            ok = False
        if not ok:
            raise ArborAuthError(f"Arbor login failed (HTTP {resp.status}): {text[:160]}")
        self._logged_in = True

    async def get_balances(self, _retry: bool = True) -> list[dict]:
        """Return [{account_id, name, balance}] for every account with a balance."""
        if not self._logged_in:
            await self._login()
        url = self._base + "/guardians/home-ui/dashboard?format=javascript"
        async with self._session.get(url, headers=self._headers()) as resp:
            status = resp.status
            text = await resp.text()
        if status in (401, 403) or text.lstrip().startswith("<"):
            # Session expired / bounced to a login page -> re-auth once.
            self._logged_in = False
            if _retry:
                await self._login()
                return await self.get_balances(_retry=False)
            raise ArborError(f"Unexpected dashboard response (HTTP {status})")
        try:
            data = json.loads(text)
        except ValueError as err:
            raise ArborError(f"Dashboard was not JSON: {text[:120]}") from err
        return self._extract(data)

    @staticmethod
    def _extract(data) -> list[dict]:
        found: list[dict] = []

        def walk(node) -> None:
            if isinstance(node, dict):
                props = node.get("props") or {}
                desc = props.get("description")
                if isinstance(desc, str) and "alance" in desc and "£" in desc:
                    m = re.search(r"(-?)£\s*(-?)([\d,]+\.?\d*)", desc)
                    if m:
                        neg = "-" if (m.group(1) or m.group(2)) else ""
                        value = float(neg + m.group(3).replace(",", ""))
                        name = re.sub(r"<[^>]+>", "", str(props.get("value", ""))).strip()
                        aid = None
                        mu = re.search(r"customer-account-id/(\d+)", str(props.get("url", "")))
                        if mu:
                            aid = mu.group(1)
                        found.append({"account_id": aid, "name": name, "balance": value})
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(data)
        return found
