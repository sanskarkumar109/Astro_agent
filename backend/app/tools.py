from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import swisseph as swe
except Exception:  # pragma: no cover - reported through tool output
    swe = None

try:
    from timezonefinder import TimezoneFinder
except Exception:  # pragma: no cover
    TimezoneFinder = None


ZODIAC = [
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
]

PLANETS = {
    "Sun": 0,
    "Moon": 1,
    "Mercury": 2,
    "Venus": 3,
    "Mars": 4,
    "Jupiter": 5,
    "Saturn": 6,
    "Uranus": 7,
    "Neptune": 8,
    "Pluto": 9,
}

KNOWN_PLACES = {
    "delhi": (28.6139, 77.2090, "Asia/Kolkata"),
    "new delhi": (28.6139, 77.2090, "Asia/Kolkata"),
    "mumbai": (19.0760, 72.8777, "Asia/Kolkata"),
    "kolkata": (22.5726, 88.3639, "Asia/Kolkata"),
    "chennai": (13.0827, 80.2707, "Asia/Kolkata"),
    "bengaluru": (12.9716, 77.5946, "Asia/Kolkata"),
    "bangalore": (12.9716, 77.5946, "Asia/Kolkata"),
    "varanasi": (25.3176, 82.9739, "Asia/Kolkata"),
    "new york": (40.7128, -74.0060, "America/New_York"),
    "london": (51.5072, -0.1276, "Europe/London"),
    "los angeles": (34.0522, -118.2437, "America/Los_Angeles"),
}


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float
    timezone: str


def _sign(longitude: float) -> dict[str, Any]:
    normalized = longitude % 360
    sign_index = int(normalized // 30)
    degree = normalized % 30
    return {
        "longitude": round(normalized, 4),
        "sign": ZODIAC[sign_index],
        "degree": round(degree, 2),
    }


def _require_ephemeris() -> None:
    if swe is None:
        raise RuntimeError("pyswisseph is not installed; install backend dependencies first.")


def geocode_place(place: str) -> dict[str, Any]:
    """Resolve a place to coordinates and timezone using curated data plus timezonefinder."""
    key = place.strip().lower()
    if not key:
        raise ValueError("Birth place is required.")

    if key in KNOWN_PLACES:
        lat, lon, tz = KNOWN_PLACES[key]
        return {
            "place": place,
            "latitude": lat,
            "longitude": lon,
            "timezone": tz,
            "source": "curated",
        }

    # Minimal fallback for "lat, lon" inputs. This avoids fake geocoding while still allowing tests.
    parts = [p.strip() for p in place.split(",")]
    if len(parts) == 2:
        try:
            lat = float(parts[0])
            lon = float(parts[1])
            tz = "UTC"
            if TimezoneFinder is not None:
                tz = TimezoneFinder().timezone_at(lat=lat, lng=lon) or "UTC"
            return {
                "place": place,
                "latitude": lat,
                "longitude": lon,
                "timezone": tz,
                "source": "coordinates",
            }
        except ValueError:
            pass

    raise ValueError(
        f"Could not geocode '{place}'. Try a known city or provide 'latitude, longitude'."
    )


def _to_julian_day(local_date: str, local_time: str, timezone: str) -> tuple[float, datetime]:
    try:
        naive = datetime.fromisoformat(f"{local_date}T{local_time}")
    except ValueError as exc:
        raise ValueError("Date must be YYYY-MM-DD and time must be HH:MM.") from exc

    if naive.year < 1800 or naive.year > 2100:
        raise ValueError("Birth dates between 1800 and 2100 are supported.")

    local_dt = naive.replace(tzinfo=ZoneInfo(timezone))
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    hour = utc_dt.hour + utc_dt.minute / 60 + utc_dt.second / 3600
    return swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, hour), utc_dt


def compute_birth_chart(date_: str, time_: str, place: str) -> dict[str, Any]:
    """Compute natal planets and Placidus houses from the Swiss Ephemeris."""
    _require_ephemeris()
    location = geocode_place(place)
    jd, utc_dt = _to_julian_day(date_, time_, location["timezone"])

    planets: dict[str, Any] = {}
    for name, planet_id in PLANETS.items():
        position, _flags = swe.calc_ut(jd, planet_id)
        planets[name] = _sign(position[0])

    cusps, ascmc = swe.houses_ex(jd, location["latitude"], location["longitude"], b"P")
    houses = {str(i + 1): _sign(cusps[i]) for i in range(12)}

    return {
        "input": {"date": date_, "time": time_, "place": place},
        "location": location,
        "utc_datetime": utc_dt.isoformat(),
        "ayanamsa": "tropical",
        "house_system": "Placidus",
        "planets": planets,
        "houses": houses,
        "angles": {"ascendant": _sign(ascmc[0]), "midheaven": _sign(ascmc[1])},
    }


def get_daily_transits(target_date: str | None, natal_chart: dict[str, Any]) -> dict[str, Any]:
    """Compute current-date transits and simple natal aspects."""
    _require_ephemeris()
    day = date.fromisoformat(target_date) if target_date else date.today()
    jd = swe.julday(day.year, day.month, day.day, 12.0)

    transits: dict[str, Any] = {}
    aspects: list[dict[str, Any]] = []
    for name, planet_id in PLANETS.items():
        position, _flags = swe.calc_ut(jd, planet_id)
        transit = _sign(position[0])
        transits[name] = transit

        natal = natal_chart.get("planets", {}).get(name)
        if natal:
            delta = abs((transit["longitude"] - natal["longitude"] + 180) % 360 - 180)
            aspect = _nearest_major_aspect(delta)
            if aspect:
                aspects.append(
                    {
                        "planet": name,
                        "aspect": aspect["name"],
                        "orb": round(aspect["orb"], 2),
                        "natal_sign": natal["sign"],
                        "transit_sign": transit["sign"],
                    }
                )

    return {"date": day.isoformat(), "transits": transits, "aspects_to_natal": aspects[:8]}


def _nearest_major_aspect(delta: float) -> dict[str, Any] | None:
    for name, angle, max_orb in [
        ("conjunction", 0, 6),
        ("sextile", 60, 4),
        ("square", 90, 5),
        ("trine", 120, 5),
        ("opposition", 180, 6),
    ]:
        orb = abs(delta - angle)
        if orb <= max_orb:
            return {"name": name, "orb": orb}
    return None


def knowledge_lookup(query: str, limit: int = 3) -> dict[str, Any]:
    notes_path = Path(__file__).resolve().parents[1] / "knowledge" / "astrology_notes.md"
    text = notes_path.read_text(encoding="utf-8")
    sections = [s.strip() for s in text.split("\n## ") if s.strip()]
    words = {w.lower().strip(".,?!:;()") for w in query.split() if len(w) > 3}

    ranked = []
    for section in sections:
        haystack = section.lower()
        score = sum(1 for word in words if word in haystack)
        if score:
            ranked.append((score, section))
    ranked.sort(reverse=True, key=lambda item: item[0])

    return {
        "query": query,
        "matches": [
            {"title": match.splitlines()[0].replace("#", "").strip(), "text": match}
            for _score, match in ranked[:limit]
        ],
    }

