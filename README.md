# Arbor (Home Assistant custom integration)

Reads a guardian's Arbor school portal account balances (e.g. meal accounts) and
exposes them as `sensor` entities in Home Assistant.

- Config flow: school Arbor URL + your guardian username/email + password (stored only in HA).
- Logs in via Arbor's JSON login endpoint and reads the guardian dashboard.
- One sensor per account discovered (device_class `monetary`, GBP).

## Install (HACS)
HACS → ⋮ → Custom repositories → add this repo, category **Integration** →
install **Arbor** → restart Home Assistant → Settings → Devices & Services → **+ Add → Arbor**.

Polling is driven by an automation (not a fixed interval) so you can schedule fetches freely.
