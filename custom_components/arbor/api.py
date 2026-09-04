"""Arbor guardian-portal client: log in and read balances, attendance, behaviour, lessons, notices."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

_LOGGER = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
    )
}


class ArborAuthError(Exception):
    """Login rejected."""


class ArborError(Exception):
    """Any other failure (incl. session expiry)."""


def _strip(html) -> str:
    """Strip HTML tags and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html or ""))).strip()


def _clean_name(name: str) -> str:
    """Tidy a till item name: proper-case ALL-CAPS/all-lower words, leave the rest."""
    words = []
    for word in name.split():
        if any(ch.isdigit() for ch in word):
            words.append(word)  # keep quantities like "x1", "2L"
        elif word.isupper() or word.islower():
            words.append(word.capitalize())
        else:
            words.append(word)
    return " ".join(words) or name


def _walk(node, match):
    """Yield every dict node in the tree for which match(node) is truthy."""
    if isinstance(node, dict):
        if match(node):
            yield node
        for value in node.values():
            yield from _walk(value, match)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, match)


class ArborApi:
    """Logs in with the guardian's credentials and scrapes the guardian portal."""

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
            "Accept": "application/json, text/plain, */*; q=0.01",
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

    async def _get_json(self, path: str):
        url = self._base + path
        async with self._session.get(url, headers=self._headers()) as resp:
            status = resp.status
            text = await resp.text()
        if status in (401, 403) or text.lstrip().startswith("<"):
            self._logged_in = False
            raise ArborError(f"session expired at {path} (HTTP {status})")
        return json.loads(text)

    async def get_data(self, _retry: bool = True) -> dict:
        """Return {'accounts': {...}, 'students': {sid: {...}}}."""
        if not self._logged_in:
            await self._login()
        try:
            dash = await self._get_json("/guardians/home-ui/dashboard?format=javascript")
        except (ArborError, ValueError):
            if _retry:
                self._logged_in = False
                await self._login()
                return await self.get_data(_retry=False)
            raise

        result = {"accounts": self._accounts(dash), "students": {}}
        for aid in list(result["accounts"].keys()):
            try:
                result["accounts"][aid]["week"] = await self._account_week(aid)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Arbor account %s week failed: %s", aid, err)
        for sid, sname in self._students(dash).items():
            entry: dict = {"name": sname}
            try:
                sdash = await self._get_json(
                    f"/guardians/home-ui/dashboard/student-id/{sid}?format=javascript"
                )
                entry["current_lesson"] = self._event(sdash, ("Current lesson", "Previous lesson"))
                entry["next_lesson"] = self._event(sdash, ("Next lesson",))
                entry["notices"] = self._notices(sdash)
                entry["assignments"] = self._assignments(sdash)
            except Exception as err:  # noqa: BLE001 - best effort per student
                _LOGGER.debug("Arbor student %s dashboard failed: %s", sid, err)
            try:
                kdata = await self._get_json(f"/guardians/student/kpis/id/{sid}/")
                entry.update(self._kpis(kdata))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Arbor student %s kpis failed: %s", sid, err)
            try:
                cal = await self._get_json(
                    f"/guardians/widget-data/get-calendar-data/student-id/{sid}/"
                )
                entry["timetable"] = self._timetable(cal)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Arbor student %s timetable failed: %s", sid, err)
            try:
                entry["timetable_week"] = await self._week_timetable(sid)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Arbor student %s week timetable failed: %s", sid, err)
            result["students"][sid] = entry
        return result

    # -- parsers ------------------------------------------------------------

    @staticmethod
    def _accounts(dash) -> dict:
        out: dict = {}
        for node in _walk(
            dash,
            lambda n: isinstance(n.get("props"), dict)
            and isinstance(n["props"].get("description"), str)
            and "alance" in n["props"]["description"]
            and "£" in n["props"]["description"],
        ):
            props = node["props"]
            m = re.search(r"(-?)£\s*(-?)([\d,]+\.?\d*)", props["description"])
            if not m:
                continue
            neg = "-" if (m.group(1) or m.group(2)) else ""
            value = float(neg + m.group(3).replace(",", ""))
            name = _strip(props.get("value"))
            aid = None
            mu = re.search(r"customer-account-id/(\d+)", str(props.get("url", "")))
            if mu:
                aid = mu.group(1)
            if aid:
                out[aid] = {"account_id": aid, "name": name, "balance": value}
        return out

    @staticmethod
    def _students(dash) -> dict:
        out: dict = {}
        for node in _walk(dash, lambda n: n.get("componentName") == "Arbor.selector.PageToggle"):
            for opt in (node.get("props", {}) or {}).get("options", []) or []:
                mu = re.search(r"student-id/(\d+)", str(opt.get("value", "")))
                if mu:
                    out[mu.group(1)] = opt.get("label") or mu.group(1)
        return out

    @staticmethod
    def _event(dash, titles) -> dict | None:
        for node in _walk(
            dash,
            lambda n: n.get("componentName") == "Arbor.container.EventBoxSection"
            and (n.get("props", {}) or {}).get("title") in titles,
        ):
            html = node.get("content")
            if not isinstance(html, str):
                continue
            mb = re.search(r"<b>(.*?)</b>", html, re.S)
            full = _strip(mb.group(1)) if mb else None
            lines = [_strip(p) for p in re.split(r"</div>", html)]
            lines = [ln for ln in lines if ln]
            if not full and lines:
                full = lines[2] if len(lines) > 2 else lines[0]
            subject = (full.split(":")[0].strip() if full else None) or full
            room = next((ln.split(":", 1)[1].strip() for ln in lines if ln.startswith("Room:")), None)
            when = lines[0] if lines and re.match(r"\d{1,2}:\d{2}", lines[0]) else None
            teacher = lines[-1] if lines and not lines[-1].startswith("Room:") else None
            return {
                "subject": subject,
                "full": full,
                "when": when,
                "room": room,
                "teacher": teacher,
                "period": (node.get("props", {}) or {}).get("title"),
                "detail": " | ".join(lines),
            }
        return None

    @staticmethod
    def _notices(dash) -> list:
        out: list = []
        for node in _walk(
            dash,
            lambda n: n.get("componentName") == "Arbor.container.Section"
            and (n.get("props", {}) or {}).get("title") == "Notices",
        ):
            for row in node.get("content") or []:
                if isinstance(row, dict):
                    txt = _strip((row.get("props", {}) or {}).get("value"))
                    if txt:
                        out.append(txt)
            break
        return out

    @staticmethod
    def _assignments(dash) -> list:
        out: list = []
        for node in _walk(
            dash,
            lambda n: n.get("componentName") == "Arbor.container.Section"
            and (n.get("props", {}) or {}).get("title") == "Assignments that are due",
        ):
            for row in node.get("content") or []:
                if not isinstance(row, dict):
                    continue
                props = (row.get("props", {}) or {})
                html = str(props.get("value", ""))
                mb = re.search(r"<b>(.*?)</b>", html, re.S)
                bold = _strip(mb.group(1)) if mb else ""
                text = _strip(html)
                md = re.search(r"Due\s+(\d{1,2})\s+(\w+)\s+(\d{4})", text)
                if not md or not bold:
                    continue  # skip the "View all assignments" link and empty rows
                code, _, title = bold.partition(":")
                day, mon, year = md.group(1), md.group(2), md.group(3)
                due_iso = f"{year}-{_MONTHS[mon]:02d}-{int(day):02d}" if mon in _MONTHS else None
                out.append(
                    {
                        "code": code.strip(),
                        "title": title.strip() or code.strip(),
                        "due": f"{int(day)} {mon} {year}",
                        "due_date": due_iso,
                        "status": _strip(props.get("description")),
                    }
                )
            break  # only the first "Assignments that are due" section
        out.sort(key=lambda a: a.get("due_date") or "9999-99-99")
        return out

    @staticmethod
    def _kpis(kdata) -> dict:
        out: dict = {}
        for item in (kdata.get("items") or []):
            fields = item.get("fields", {}) or {}
            title = str((fields.get("title", {}) or {}).get("value", "")).lower()
            html = (fields.get("html", {}) or {}).get("value", "")
            m = re.search(r"measure-value[^>]*>\s*(-?\d+)", str(html))
            if not m:
                continue
            val = int(m.group(1))
            if "attendance" in title:
                out["attendance"] = val
            elif "positive points" in title:
                out["positive_points"] = val
            elif "positive behav" in title:
                out["positive_incidents"] = val
            elif "negative behav" in title:
                out["negative_incidents"] = val
            elif "neutral behav" in title:
                out["neutral_incidents"] = val
        return out

    async def _week_timetable(self, sid: str) -> dict:
        """Fetch Mon-Fri of the current week, keyed by ISO date."""
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        week: dict = {}
        for offset in range(5):
            day = monday + timedelta(days=offset)
            ds = day.isoformat()
            try:
                cal = await self._get_json(
                    f"/guardians/widget-data/get-calendar-data/student-id/{sid}/date/{ds}/"
                )
                week[ds] = self._timetable(cal)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Arbor week timetable %s %s failed: %s", sid, ds, err)
                week[ds] = []
        return week

    @staticmethod
    def _timetable(cal) -> list:
        out: list = []
        for item in (cal.get("items") or []):
            fields = item.get("fields", {}) or {}
            start = str((fields.get("start_datetime", {}) or {}).get("value", ""))
            end = str((fields.get("end_datetime", {}) or {}).get("value", ""))
            title = str((fields.get("title", {}) or {}).get("value", ""))
            room = str((fields.get("location", {}) or {}).get("value", ""))
            st = start[11:16] if len(start) >= 16 else ""
            et = end[11:16] if len(end) >= 16 else ""
            out.append(
                {
                    "time": f"{st}-{et}" if st and et else (st or et),
                    "start": st,
                    "end": et,
                    "subject": title.split(":")[0].strip() if title else title,
                    "full": title,
                    "room": room,
                }
            )
        return out

    async def _account_week(self, aid: str) -> dict:
        dash = await self._get_json(
            f"/guardians/customer-account-ui/dashboard/customer-account-id/{aid}?format=javascript"
        )
        week = self._parse_week(dash)
        for day in week["days"]:
            if day["amount"] > 0 and day["date"]:
                try:
                    detail = await self._get_json(
                        f"/guardians/customer-account-ui/view-payments-on-date/date/{day['date']}"
                        f"/customer-account-id/{aid}?format=javascript"
                    )
                    day["items"] = self._payment_items(detail)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Arbor payments %s %s failed: %s", aid, day["date"], err)
        return week

    @staticmethod
    def _payment_items(detail) -> list:
        items: list = []
        for node in _walk(detail, lambda n: n.get("componentName") == "Arbor.container.PropertyRow"):
            props = (node.get("props", {}) or {})
            label = _strip(props.get("fieldLabel"))
            val = str(props.get("value", ""))
            if not label or label.lower().startswith("epos") or "0.00" not in val:
                continue
            name, price = label, None
            m = re.search(r"(£\s*)?(\d+(?:\.\d{1,2})?)\s*$", label)
            if m:
                name = label[: m.start()].strip(" -£")
                num = float(m.group(2))
                # £-prefixed or decimal => pounds; a bare whole number => pence
                price = num if (m.group(1) or "." in m.group(2)) else round(num / 100.0, 2)
            items.append({"name": _clean_name(name) if name else label, "price": price})
        return items

    @staticmethod
    def _parse_week(dash) -> dict:
        week: dict = {"start": None, "total": None, "days": []}
        for node in _walk(
            dash,
            lambda n: n.get("componentName") == "Arbor.container.Section"
            and str((n.get("props", {}) or {}).get("title", "")).startswith("Week beginning"),
        ):
            title = str(node["props"]["title"])
            mt = re.search(r"Week beginning\s+(.+?):\s*£([\d.]+)", title)
            if mt:
                week["start"] = mt.group(1).strip()
                week["total"] = float(mt.group(2))
            for row in node.get("content") or []:
                props = (row.get("props", {}) or {})
                amt = re.search(r"£\s*([\d.]+)", str(props.get("value", "")))
                md = re.search(r"date/(\d{4}-\d{2}-\d{2})", str(props.get("url", "")))
                week["days"].append(
                    {
                        "day": props.get("fieldLabel"),
                        "date": md.group(1) if md else None,
                        "amount": float(amt.group(1)) if amt else 0.0,
                        "items": [],
                    }
                )
            break  # the first "Week beginning" section is the current week
        return week
