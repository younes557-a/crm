"""Identification d'une personne à partir d'un texte (courrier scanné, e-mail).

Principe : on cherche dans le texte le nom de famille de chaque contact
(comparaison approximative pour tolérer les erreurs d'OCR), puis on vérifie la
présence du prénom (ou de son initiale) à proximité. L'adresse e-mail, le
téléphone, le code postal, la ville et la rue apportent des indices
supplémentaires. Le score final est compris entre 0 et 100.
"""
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .text import digits, normalize

NAME_MIN_RATIO = 82  # tolérance aux fautes d'OCR sur un mot
PROXIMITY = 4  # distance max (en mots) entre prénom et nom
CIVILITY_WORDS = {"m", "mr", "mme", "mlle", "monsieur", "madame", "mademoiselle", "dr", "me"}


@dataclass
class Candidate:
    contact: object
    score: float
    reasons: list = field(default_factory=list)

    def as_dict(self):
        return {
            "contact_id": self.contact.id,
            "name": self.contact.full_name,
            "score": round(self.score, 1),
            "reasons": self.reasons,
        }


def _find_sequence(words, target, min_ratio, cache):
    """Positions où la suite de mots `target` apparaît (approximativement)."""
    n = len(target)
    if not n or len(words) < n:
        return []
    joined_target = " ".join(target)
    if n not in cache:
        cache[n] = [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]
    windows = cache[n]
    # Les mots très courts doivent correspondre exactement (évite les faux positifs).
    cutoff = 100 if len(joined_target) <= 3 else min_ratio
    matches = process.extract(
        joined_target, windows, scorer=fuzz.ratio, score_cutoff=cutoff, limit=None
    )
    return [(idx, score) for _, score, idx in matches]


def _first_name_score(words, start, end, first_tokens):
    if not first_tokens:
        return 0, None
    lo, hi = max(0, start - PROXIMITY), min(len(words), end + PROXIMITY)
    region = words[lo:start] + words[end:hi]
    if not region:
        return 0, None
    best, how = 0, None
    target = " ".join(first_tokens)
    n = len(first_tokens)
    for i in range(len(region) - n + 1):
        ratio = fuzz.ratio(target, " ".join(region[i:i + n]))
        if ratio > best:
            best, how = ratio, "prénom"
    # Initiale seule (« J. Dupont ») : indice plus faible.
    initial = first_tokens[0][0]
    if best < NAME_MIN_RATIO and initial in region:
        return 70, "initiale du prénom"
    return (best, how) if best >= NAME_MIN_RATIO else (0, None)


def score_contact(contact, ctx):
    words, norm_text = ctx["words"], ctx["norm_text"]
    score = 0.0
    reasons = []

    email = (contact.email or "").strip().lower()
    if email and email in ctx["raw_lower"]:
        score = 100.0
        reasons.append("adresse e-mail")

    phone = digits(contact.phone)
    if len(phone) >= 9 and phone[-9:] in ctx["digits"]:
        score = max(score, 95.0)
        reasons.append("numéro de téléphone")

    last_tokens = normalize(contact.last_name).split()
    first_tokens = normalize(contact.first_name).split()
    name_score = 0.0
    for idx, ln_ratio in _find_sequence(words, last_tokens, NAME_MIN_RATIO, ctx["windows"]):
        end = idx + len(last_tokens)
        fn_ratio, how = _first_name_score(words, idx, end, first_tokens)
        if fn_ratio:
            candidate = 0.55 * ln_ratio + 0.45 * fn_ratio
            label = f"nom + {how}"
        else:
            # Nom seul : jamais suffisant pour un rattachement automatique,
            # sauf s'il est précédé d'une civilité (« Madame Durand »).
            before = words[idx - 1] if idx > 0 else ""
            candidate = ln_ratio * (0.7 if before in CIVILITY_WORDS else 0.55)
            label = "nom seul"
        if candidate > name_score:
            name_score = candidate
            name_reason = label
    if name_score:
        reasons.append(name_reason)

    # Indices d'adresse : ne font que conforter une correspondance de nom.
    bonus = 0.0
    if name_score:
        if contact.postal_code and contact.postal_code.strip() in ctx["numbers"]:
            bonus += 6
            reasons.append("code postal")
        city = normalize(contact.city)
        if city and f" {city} " in f" {norm_text} ":
            bonus += 4
            reasons.append("ville")
        street = normalize(contact.address)
        if street and len(street) > 6 and fuzz.partial_ratio(street, norm_text) >= 85:
            bonus += 8
            reasons.append("adresse")

    score = max(score, min(100.0, name_score + bonus))
    return score, reasons


def find_contacts(text, contacts, limit=5, min_score=40):
    """Classe les contacts susceptibles d'être mentionnés dans `text`."""
    norm_text = normalize(text)
    words = norm_text.split()
    if not words:
        return []
    ctx = {
        "words": words,
        "norm_text": norm_text,
        "digits": digits(text),
        "numbers": {w for w in words if w.isdigit()},
        "raw_lower": (text or "").lower(),
        "windows": {},
    }
    results = []
    for contact in contacts:
        score, reasons = score_contact(contact, ctx)
        if score >= min_score:
            results.append(Candidate(contact, score, reasons))
    results.sort(key=lambda c: c.score, reverse=True)
    return results[:limit]


def best_match(candidates, threshold):
    """Retourne le candidat à rattacher automatiquement, ou None si ambigu."""
    if not candidates or candidates[0].score < threshold:
        return None
    if len(candidates) > 1 and candidates[0].score - candidates[1].score < 5:
        return None  # deux personnes aussi probables : vérification humaine
    return candidates[0]
