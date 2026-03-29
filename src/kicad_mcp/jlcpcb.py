"""JLCPCB parts search via the JLCSearch public API (jlcsearch.tscircuit.com).

No authentication required. Uses stdlib only.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


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
    """Search JLCPCB parts by keyword."""
    params: dict[str, str] = {"q": query, "limit": str(limit)}
    if category:
        params["category"] = category
    if in_stock:
        params["in_stock"] = "true"

    url = f"https://jlcsearch.tscircuit.com/components/list.json?{urllib.parse.urlencode(params)}"

    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    return [_parse_part(p) for p in data.get("components", [])]


def get_part(lcsc_number: int | str) -> JLCPart | None:
    """Get a specific part by LCSC number (e.g., 21190 or "C21190")."""
    lcsc_str = str(lcsc_number).lstrip("Cc")

    url = f"https://jlcsearch.tscircuit.com/components/list.json?q=C{lcsc_str}"

    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    for c in data.get("components", []):
        if str(c.get("lcsc", "")) == lcsc_str:
            return _parse_part(c)
    return None


def list_categories() -> list[dict]:
    """List available JLCPCB part categories."""
    url = "https://jlcsearch.tscircuit.com/categories/list.json"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data.get("categories", [])


def _parse_part(raw: dict) -> JLCPart:
    price_data = raw.get("price", [])
    if isinstance(price_data, str):
        try:
            price_data = json.loads(price_data)
        except (json.JSONDecodeError, TypeError):
            price_data = []
    return JLCPart(
        lcsc=raw.get("lcsc", 0),
        mfr=raw.get("mfr", ""),
        package=raw.get("package", ""),
        description=raw.get("description", ""),
        stock=raw.get("stock", 0),
        price=price_data if isinstance(price_data, list) else [],
        category=raw.get("category", ""),
        subcategory=raw.get("subcategory", ""),
        is_basic=raw.get("is_basic", False),
        is_preferred=raw.get("is_preferred", False),
    )
