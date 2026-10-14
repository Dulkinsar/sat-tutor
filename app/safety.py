"""Crisis screen that runs before the model sees a message: local patterns (EN/RU/KZ) plus
OpenAI moderation. Covers suicide and self-harm only.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("tutor.safety")

MODERATION_MODEL = "omni-moderation-latest"
SELF_HARM = ("self-harm", "self-harm/intent", "self-harm/instructions")

SAFETY_REPLY = (
    "It sounds like you're carrying something really heavy right now, and I'm glad you said it. "
    "I'm an SAT tutor, and you deserve support from someone trained for this.\n\n"
    "If you might be in danger right now, please call your local emergency number.\n"
    "You can find a free, confidential helpline in your country at https://findahelpline.com.\n"
    "Talking to a parent, a teacher, a school counselor or another adult you trust can help too.\n\n"
    "I'll be here for SAT questions whenever you want to come back to them."
)
CARE_REPLY = (
    "I'm still here, and I care about how you're doing. Please reach out to someone who can help right now: "
    "your local emergency number if you might be in danger, a helpline from https://findahelpline.com, "
    "or an adult you trust. We can pick up SAT practice a little later."
)

_EN = [
    r"\b(kill|killing|kil|hurt|hurting|harm|harming|cut|cutting|injure|end|ending)\s+my\s*self\b",
    r"^\W*kms\W*$",  # "kms" on its own; elsewhere it is usually kilometres
    r"\bi\s*('?ll|\s+will|\s+want\s+to|\s+wanna|\s+might|\s+should)?\s+kms\b",
    r"\bsu[i1]?c[i1]d(e|al)\b",
    r"\b(end|ending|take|taking)\s+my\s+(own\s+)?life\b",
    r"\bend(ing)?\s+it\s+all\b",
    r"\bi(\s*'?m|\s+am)?\s+(just\s+|really\s+|honestly\s+)?(want|wanna|wish|going|plan|planning)\s+(to\s+)?(d[i1]e|be\s+dead)\b",
    r"\bi\s+wish\s+i\s+(was|were)\s+(dead|never\s+born)\b",
    r"\b(i'?m|i\s+am)\s+(better\s+off\s+dead|thinking\s+of\s+(killing|ending|hurting)\s+(myself|it))\b",
    r"\bdon'?t\s+want\s+to\s+(be\s+alive|live|exist|wake\s+up)\b",
    r"\bi\s+(keep\s+)?(think|thinking)\s+about\s+(dying|killing\s+myself|ending\s+it)\b",
    r"\b(no|any|what'?s\s+the)\s+(reason|point)\s+(to|in|of)\s+(live|living|being\s+alive|going\s+on)\b",
    r"\bbetter\s+off\s+(dead|without\s+me)\b",
    r"\bnot\s+worth\s+living\b",
    r"\bself[-\s]?harm(ing)?\b",
]
_RU_KZ = [
    r"поконч(ить|у|ит)\s+с\s+собой",
    r"суицид",
    r"(не\s+хочу\s+(больше\s+)?жить|жить\s+не\s+хочу)",
    r"(убить|убью)\s+себя|себя\s+убью",
    r"наложить\s+на\s+себя\s+руки",
    r"хочу\s+умереть",
    r"өмір\s+сүргім\s+келмейді",
    r"өлгім\s+келеді",
    r"өзімді\s+өлтір",
]
_EN_RE = [re.compile(p, re.IGNORECASE) for p in _EN]
_RU_KZ_RE = [re.compile(p, re.IGNORECASE) for p in _RU_KZ]

# Cyrillic and Greek letters that render like Latin ones.
_FOLD = str.maketrans("аеіорсухкмтнвАЕІОРСУХКМТНВο", "aeiopcyxkmthbAEIOPCYXKMTHBo")


@dataclass
class ScreenResult:
    flagged: bool
    categories: list[str] = field(default_factory=list)
    source: str = ""


def local_screen(text: str) -> ScreenResult:
    raw = unicodedata.normalize("NFKC", text)
    folded = raw.translate(_FOLD)
    hit = any(p.search(folded) for p in _EN_RE) or any(p.search(raw) for p in _RU_KZ_RE)
    return ScreenResult(hit, ["pattern"] if hit else [], "local" if hit else "")


def moderation_screen(text: str, client) -> ScreenResult:
    threshold = float(os.getenv("SAFETY_SCORE_THRESHOLD", "0.3"))
    result = client.moderations.create(model=MODERATION_MODEL, input=text).results[0]
    categories = result.categories.model_dump(by_alias=True)
    scores = result.category_scores.model_dump(by_alias=True)
    hit = [c for c in SELF_HARM if categories.get(c) or (scores.get(c) or 0) >= threshold]
    return ScreenResult(bool(hit), hit, "moderation" if hit else "")


def screen(text: str, client=None) -> ScreenResult:
    local = local_screen(text)
    if local.flagged or client is None:
        return local
    try:
        return moderation_screen(text, client)
    except Exception as exc:  # the screen must never take the tutor down
        log.warning("moderation call failed, using local screen only: %s", exc)
        return local


def care_minutes() -> int:
    return int(os.getenv("SAFETY_CARE_MINUTES", "30"))


def alert_mentor(session_id: str, source: str) -> None:
    """POST a text-free alert to SAFETY_WEBHOOK_URL in the background, if configured."""
    url = os.getenv("SAFETY_WEBHOOK_URL")
    if not url:
        return
    payload = {
        "event": "crisis_flag",
        "session": session_id,
        "source": source,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    def send() -> None:
        import httpx

        try:
            httpx.post(url, json=payload, timeout=5).raise_for_status()
        except Exception as exc:
            log.error("mentor alert failed: %s", exc)

    threading.Thread(target=send, daemon=True).start()
