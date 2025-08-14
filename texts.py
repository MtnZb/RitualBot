# texts.py
import json
from pathlib import Path

_LANG = "ru"
_DATA = None
_PATH = Path("data") / f"texts_{_LANG}.json"

def _load():
    global _DATA
    if _DATA is None:
        try:
            _DATA = json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:
            _DATA = {}
    return _DATA

def t(key: str, **kwargs) -> str:
    data = _load()
    cur = data
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            cur = None
            break
    text = cur if isinstance(cur, str) else key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            text = f"{text}  [{key}]"
    return text
