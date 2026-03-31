"""JLCPCB parts search via the JLCSearch public API (jlcsearch.tscircuit.com).

No authentication required. Uses stdlib only.

The API has category-specific endpoints (resistors, capacitors, etc.) that
support parametric filtering, and a generic /components endpoint for broad
searches. Category endpoints return better results for known part types.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

_BASE_URL = "https://jlcsearch.tscircuit.com"
_HEADERS = {"User-Agent": "kicad-mcp/0.1"}


def _api_get(path: str, params: dict[str, str] | None = None) -> dict:
    """Make a GET request to the JLCSearch API."""
    url = f"{_BASE_URL}/{path}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


@dataclass
class JLCPart:
    lcsc: int
    mfr: str
    package: str
    description: str
    stock: int
    price: list[dict] = field(default_factory=list)
    category: str = ""
    subcategory: str = ""
    is_basic: bool = False
    is_preferred: bool = False


def search_parts(
    query: str,
    category: str | None = None,
    in_stock: bool = True,
    limit: int = 30,
) -> list[JLCPart]:
    """Search JLCPCB parts by keyword.

    If category is specified and matches a known endpoint (resistors,
    capacitors, inductors, etc.), uses the category-specific API for
    better results. Otherwise uses the generic components endpoint.
    """
    params: dict[str, str] = {"q": query, "limit": str(limit)}
    if in_stock:
        params["in_stock"] = "true"

    # Category-specific endpoints have better search
    category_endpoints: dict[str, str] = {
        "resistors": "resistors",
        "capacitors": "capacitors",
        "inductors": "inductors",
        "diodes": "diodes",
        "transistors": "transistors",
        "leds": "leds",
        "microcontrollers": "microcontrollers",
        "connectors": "connectors",
    }

    endpoint = "components"
    if category:
        cat_lower = category.lower().rstrip("s") + "s"  # normalize plurals
        if cat_lower in category_endpoints:
            endpoint = category_endpoints[cat_lower]

    data = _api_get(f"{endpoint}/list.json", params)

    # Response key matches the endpoint name
    parts_list = data.get(endpoint, data.get("components", []))
    return [_parse_part(p) for p in parts_list[:limit]]


def get_part(lcsc_number: int | str) -> JLCPart | None:
    """Get a specific part by LCSC number (e.g., 21190 or "C21190")."""
    lcsc_str = str(lcsc_number).lstrip("Cc")

    data = _api_get("components/list.json", {"q": f"C{lcsc_str}", "limit": "10"})

    for c in data.get("components", []):
        if str(c.get("lcsc", "")) == lcsc_str:
            return _parse_part(c)
    return None


def list_categories() -> list[dict]:
    """List available JLCPCB part categories."""
    data = _api_get("categories/list.json")
    return data.get("categories", [])


def _parse_part(raw: dict) -> JLCPart:
    price_data = raw.get("price", [])
    if isinstance(price_data, str):
        try:
            price_data = json.loads(price_data)
        except (json.JSONDecodeError, TypeError):
            price_data = []

    # Build description from available fields
    desc = raw.get("description", "")
    if not desc:
        # Some category endpoints provide specific fields instead of description
        parts = []
        for field_name in ("resistance", "capacitance", "inductance", "voltage_rating",
                          "power_rating", "tolerance", "forward_voltage"):
            val = raw.get(field_name)
            if val:
                parts.append(str(val))
        if parts:
            desc = ", ".join(parts)

    return JLCPart(
        lcsc=raw.get("lcsc", 0),
        mfr=raw.get("mfr", ""),
        package=raw.get("package", ""),
        description=desc,
        stock=raw.get("stock", 0),
        price=price_data if isinstance(price_data, list) else [],
        category=raw.get("category", ""),
        subcategory=raw.get("subcategory", ""),
        is_basic=raw.get("is_basic", False),
        is_preferred=raw.get("is_preferred", False),
    )
