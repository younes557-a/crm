import re
import unicodedata

WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(text):
    """Minuscules, sans accents, ponctuation remplacée par des espaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("œ", "oe").replace("æ", "ae")
    return " ".join(WORD_RE.findall(text))


def tokens(text):
    return WORD_RE.findall(normalize(text))


def digits(text):
    return re.sub(r"\D", "", text or "")
