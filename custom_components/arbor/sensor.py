"""Arbor sensors: account balances + per-student attendance, behaviour, lessons, notices."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

# key, label, unit, icon
NUM_METRICS = [
    ("attendance", "Attendance", "%", "mdi:calendar-check"),
    ("positive_points", "Positive Points", None, "mdi:star-outline"),
    ("positive_incidents", "Positive Incidents", None, "mdi:emoticon-happy-outline"),
    ("negative_incidents", "Negative Incidents", None, "mdi:emoticon-sad-outline"),
    ("neutral_incidents", "Neutral Incidents", None, "mdi:emoticon-neutral-outline"),
]
# key, label, icon
LESSON_METRICS = [
    ("current_lesson", "Current Lesson", "mdi:school-outline"),
    ("next_lesson", "Next Lesson", "mdi:clock-outline"),
]


def _student_device(sid: str, name: str) -> dict:
    return {"identifiers": {(DOMAIN, f"student_{sid}")}, "name": name, "manufacturer": "Arbor"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    def _sync() -> None:
        data = coordinator.data or {}
        students = data.get("students", {}) or {}
        new: list[SensorEntity] = []

        for sid, sdata in students.items():
            name = sdata.get("name") or f"Student {sid}"
            for key, label, unit, icon in NUM_METRICS:
                uid = f"arbor_{sid}_{key}"
                if uid not in known:
                    known.add(uid)
                    new.append(ArborNumSensor(coordinator, sid, name, key, label, unit, icon))
            for key, label, icon in LESSON_METRICS:
                uid = f"arbor_{sid}_{key}"
                if uid not in known:
                    known.add(uid)
                    new.append(ArborLessonSensor(coordinator, sid, name, key, label, icon))
            uid = f"arbor_{sid}_notices"
            if uid not in known:
                known.add(uid)
                new.append(ArborNoticesSensor(coordinator, sid, name))
            uid = f"arbor_{sid}_timetable"
            if uid not in known:
                known.add(uid)
                new.append(ArborTimetableSensor(coordinator, sid, name))

        for aid, acc in (data.get("accounts", {}) or {}).items():
            uid = f"arbor_{aid}"
            if uid not in known:
                known.add(uid)
                new.append(ArborBalanceSensor(coordinator, aid, acc.get("name") or f"Account {aid}", students))

        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class _StudentBase(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, sid: str, sname: str) -> None:
        super().__init__(coordinator)
        self._sid = sid
        self._attr_device_info = _student_device(sid, sname)

    def _sdata(self) -> dict:
        return ((self.coordinator.data or {}).get("students", {}) or {}).get(self._sid, {}) or {}


class ArborNumSensor(_StudentBase):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, sid, sname, key, label, unit, icon) -> None:
        super().__init__(coordinator, sid, sname)
        self._key = key
        self._attr_unique_id = f"arbor_{sid}_{key}"
        self._attr_name = f"{sname} {label}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self):
        return self._sdata().get(self._key)


class ArborLessonSensor(_StudentBase):
    def __init__(self, coordinator, sid, sname, key, label, icon) -> None:
        super().__init__(coordinator, sid, sname)
        self._key = key
        self._attr_unique_id = f"arbor_{sid}_{key}"
        self._attr_name = f"{sname} {label}"
        self._attr_icon = icon

    @property
    def native_value(self):
        les = self._sdata().get(self._key)
        return les.get("subject") if isinstance(les, dict) else None

    @property
    def extra_state_attributes(self) -> dict:
        les = self._sdata().get(self._key)
        if isinstance(les, dict):
            return {k: les.get(k) for k in ("full", "when", "room", "teacher", "period", "detail")}
        return {}


class ArborNoticesSensor(_StudentBase):
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator, sid, sname) -> None:
        super().__init__(coordinator, sid, sname)
        self._attr_unique_id = f"arbor_{sid}_notices"
        self._attr_name = f"{sname} Notices"

    @property
    def native_value(self) -> int:
        return len(self._sdata().get("notices") or [])

    @property
    def extra_state_attributes(self) -> dict:
        return {"notices": self._sdata().get("notices") or []}


class ArborTimetableSensor(_StudentBase):
    _attr_icon = "mdi:timetable"

    def __init__(self, coordinator, sid, sname) -> None:
        super().__init__(coordinator, sid, sname)
        self._attr_unique_id = f"arbor_{sid}_timetable"
        self._attr_name = f"{sname} Timetable"

    @property
    def native_value(self) -> int:
        return len(self._sdata().get("timetable") or [])

    @property
    def extra_state_attributes(self) -> dict:
        return {"lessons": self._sdata().get("timetable") or []}


class ArborBalanceSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "GBP"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator, aid: str, name: str, students: dict) -> None:
        super().__init__(coordinator)
        self._aid = aid
        self._attr_unique_id = f"arbor_{aid}"
        self._attr_name = name
        device = {"identifiers": {(DOMAIN, "arbor_accounts")}, "name": "Arbor", "manufacturer": "Arbor"}
        for sid, sdata in (students or {}).items():
            sname = sdata.get("name") or ""
            if sname and name.startswith(sname):
                device = _student_device(sid, sname)
                break
        self._attr_device_info = device

    @property
    def native_value(self):
        acc = ((self.coordinator.data or {}).get("accounts", {}) or {}).get(self._aid)
        return acc.get("balance") if acc else None

    @property
    def extra_state_attributes(self) -> dict:
        acc = ((self.coordinator.data or {}).get("accounts", {}) or {}).get(self._aid) or {}
        return {"account_id": self._aid, "account": acc.get("name")}
