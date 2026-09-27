from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.sentiment import analyze
from app.services.summary import summarize


def test_sentiment():
    assert analyze("Merci beaucoup pour votre soutien, bravo à toute l'équipe !")[0] == "positive"
    assert analyze("C'est inacceptable, je suis très déçu et en colère.")[0] == "negative"
    assert analyze("Je ne suis pas satisfait de la réponse.")[0] == "negative"
    assert analyze("Rendez-vous mardi à 14h en mairie.")[0] == "neutral"
    assert analyze("")[0] == "neutral"


def fake(sentiment, days_ago, kind="appel"):
    return SimpleNamespace(
        sentiment=sentiment, kind=kind, date=datetime.utcnow() - timedelta(days=days_ago),
        kind_label="Appel téléphonique", sentiment_label={"positive": "Positive", "neutral": "Neutre", "negative": "Négative"}[sentiment],
    )


def test_summary():
    items = [fake("positive", 1), fake("positive", 5), fake("positive", 10),
             fake("negative", 100), fake("negative", 200)]
    s = summarize(items)
    assert s["total"] == 5
    assert s["counts"] == {"positive": 3, "neutral": 0, "negative": 2}
    assert s["overall"] == "positive"
    assert s["trend"] == "en amélioration"
    assert "5 interactions" in s["text"]


def test_summary_empty():
    assert summarize([])["stance"] == "Aucune interaction"
