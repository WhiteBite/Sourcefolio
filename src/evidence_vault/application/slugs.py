from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

_CYRILLIC = {
    "\u0430": "a",
    "\u0431": "b",
    "\u0432": "v",
    "\u0433": "g",
    "\u0434": "d",
    "\u0435": "e",
    "\u0451": "e",
    "\u0436": "zh",
    "\u0437": "z",
    "\u0438": "i",
    "\u0439": "y",
    "\u043a": "k",
    "\u043b": "l",
    "\u043c": "m",
    "\u043d": "n",
    "\u043e": "o",
    "\u043f": "p",
    "\u0440": "r",
    "\u0441": "s",
    "\u0442": "t",
    "\u0443": "u",
    "\u0444": "f",
    "\u0445": "h",
    "\u0446": "ts",
    "\u0447": "ch",
    "\u0448": "sh",
    "\u0449": "sch",
    "\u044a": "",
    "\u044b": "y",
    "\u044c": "",
    "\u044d": "e",
    "\u044e": "yu",
    "\u044f": "ya",
}


def slugify(name: str) -> str:
    lowered = name.strip().lower()
    transliterated = "".join(_CYRILLIC.get(character, character) for character in lowered)
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    if not slug:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        slug = f"collection-{digest}"
    return slug


def unique_slugs(names: Iterable[str]) -> dict[str, str]:
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted(names, key=str.casefold):
        slug = slugify(name)
        if slug in used:
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
            slug = f"{slug}-{digest}"
        used.add(slug)
        slugs[name] = slug
    return slugs
