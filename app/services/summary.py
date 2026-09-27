"""Synthèse des interactions d'un contact."""
from collections import Counter
from datetime import datetime

from ..models import INTERACTION_KINDS, SENTIMENTS

WEIGHT = {"positive": 1, "neutral": 0, "negative": -1}


def _plural(n, word):
    return f"{n} {word}{'s' if n > 1 else ''}"


def summarize(interactions):
    """`interactions` : liste triée de la plus récente à la plus ancienne."""
    total = len(interactions)
    counts = Counter(i.sentiment for i in interactions)
    result = {
        "total": total,
        "counts": {key: counts.get(key, 0) for key in SENTIMENTS},
        "percent": {
            key: round(100 * counts.get(key, 0) / total) if total else 0 for key in SENTIMENTS
        },
        "by_kind": Counter(INTERACTION_KINDS.get(i.kind, i.kind) for i in interactions).most_common(),
        "last": interactions[0] if interactions else None,
        "first": interactions[-1] if interactions else None,
        "score": 0.0,
        "overall": "neutral",
        "stance": "Aucune interaction",
        "trend": None,
        "text": "Aucune interaction enregistrée pour le moment.",
    }
    if not total:
        return result

    # Score pondéré : les échanges récents comptent davantage.
    now = datetime.utcnow()
    num = den = 0.0
    for inter in interactions:
        age_days = max(0, (now - inter.date).days)
        weight = 1 / (1 + age_days / 180)
        num += WEIGHT.get(inter.sentiment, 0) * weight
        den += weight
    score = num / den if den else 0.0
    result["score"] = round(score, 2)
    if score >= 0.25:
        result["overall"], result["stance"] = "positive", "Plutôt favorable"
    elif score <= -0.25:
        result["overall"], result["stance"] = "negative", "Plutôt défavorable"
    else:
        result["overall"], result["stance"] = "neutral", "Neutre / indécis"

    # Tendance : 3 dernières interactions comparées aux précédentes.
    if total >= 4:
        recent = sum(WEIGHT[i.sentiment] for i in interactions[:3]) / 3
        older = sum(WEIGHT[i.sentiment] for i in interactions[3:]) / (total - 3)
        if recent - older >= 0.3:
            result["trend"] = "en amélioration"
        elif older - recent >= 0.3:
            result["trend"] = "en dégradation"
        else:
            result["trend"] = "stable"

    c = result["counts"]
    last = result["last"]
    parts = [
        f"{_plural(total, 'interaction')} depuis le {result['first'].date:%d/%m/%Y}"
        f" : {_plural(c['positive'], 'positive')}, {_plural(c['neutral'], 'neutre')},"
        f" {_plural(c['negative'], 'négative')}.",
        f"Dernier contact le {last.date:%d/%m/%Y} ({last.kind_label.lower()},"
        f" {last.sentiment_label.lower()}).",
        f"Appréciation générale : {result['stance'].lower()}.",
    ]
    if result["trend"]:
        parts.append(f"Tendance récente : {result['trend']}.")
    result["text"] = " ".join(parts)
    return result
