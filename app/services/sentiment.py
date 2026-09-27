"""Analyse de tonalité simple (lexique français + gestion de la négation).

Pas de dépendance externe ni d'appel réseau : les données restent sur le
serveur. Le résultat est une suggestion que l'utilisateur peut corriger.
"""
from .text import tokens

# Racines normalisées (sans accents). Un mot correspond s'il commence par la racine.
POSITIVE = {
    "merci": 2, "remerci": 2, "bravo": 2, "felicit": 2, "soutien": 2, "soutiens": 2,
    "soutenir": 2, "favorable": 2, "excellent": 2, "formidable": 2, "genial": 2,
    "super": 1, "satisfait": 2, "content": 1, "heureux": 1, "heureuse": 1, "ravi": 2,
    "apprecie": 2, "enthousias": 2, "confiance": 1, "accord": 1, "adhere": 2,
    "adhesion": 2, "benevol": 2, "engag": 1, "aider": 1, "volontiers": 1, "bien": 1,
    "positif": 1, "positive": 1, "interesse": 1, "partage": 1, "convaincu": 2,
    "voterai": 1, "encourag": 2, "qualite": 1, "efficace": 1, "reactif": 1,
    "top": 1, "parfait": 2, "agreable": 1, "chaleureu": 1, "bonne": 1, "bon": 1,
}
NEGATIVE = {
    "mecontent": 2, "colere": 2, "decu": 2, "decue": 2, "deception": 2, "inacceptable": 3,
    "scandal": 3, "plainte": 2, "plaindre": 2, "probleme": 1, "oppose": 2, "opposition": 1,
    "refus": 2, "honte": 3, "nul": 2, "nulle": 2, "mauvais": 2, "mauvaise": 2,
    "incompeten": 2, "inquiet": 1, "inquiete": 1, "insatisf": 2, "arretez": 2,
    "desinscri": 2, "harcel": 3, "retirer": 1, "supprim": 1, "agac": 2, "furieux": 3,
    "revolt": 2, "degout": 3, "lamentable": 3, "mensong": 2, "menteur": 3, "voleur": 3,
    "corrompu": 3, "abandon": 1, "injust": 2, "catastroph": 2, "grave": 1, "lassant": 1,
    "marre": 2, "rien": 1, "jamais": 1, "contre": 1, "hostile": 2, "critique": 1,
}
NEGATORS = {"pas", "plus", "jamais", "aucun", "aucune", "ni", "sans", "guere", "non"}
NEGATION_WINDOW = 3


def _lookup(word, lexicon):
    if word in lexicon:
        return lexicon[word]
    for root, weight in lexicon.items():
        if len(root) >= 5 and word.startswith(root):
            return weight
    return 0


def analyze(text):
    """Retourne (sentiment, score) avec sentiment ∈ positive/neutral/negative.

    Le score est normalisé entre -1 et 1.
    """
    words = tokens(text)
    if not words:
        return "neutral", 0.0
    total = 0.0
    hits = 0
    for i, word in enumerate(words):
        pos = _lookup(word, POSITIVE)
        neg = _lookup(word, NEGATIVE)
        if word in NEGATORS and word != "jamais":
            continue
        value = pos - neg
        if not value:
            continue
        window = words[max(0, i - NEGATION_WINDOW):i]
        if any(w in NEGATORS for w in window):
            value = -value
        total += value
        hits += 1
    if not hits:
        return "neutral", 0.0
    score = max(-1.0, min(1.0, total / (hits + 2)))
    if score >= 0.2:
        return "positive", round(score, 2)
    if score <= -0.2:
        return "negative", round(score, 2)
    return "neutral", round(score, 2)
