"""Tout ce qui parle au modèle de langage (Groq via le SDK OpenAI)."""
import os

from dotenv import load_dotenv
from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                    AuthenticationError, BadRequestError, NotFoundError,
                    OpenAI, PermissionDeniedError, RateLimitError)

from errors import AppError

load_dotenv()

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
BASE_URL = "https://api.groq.com/openai/v1"

# Fonctionnalité 2 : chaque domaine = un "prompt système" différent.
DOMAINS = {
    "general": {
        "label": "Général",
        "prompt": "Tu es un assistant polyvalent. Réponds de façon claire et structurée.",
    },
    "informatique": {
        "label": "Informatique",
        "prompt": "Tu es un ingénieur logiciel senior. Donne des explications techniques "
                  "précises, des exemples de code commentés et signale les bonnes pratiques.",
    },
    "finance": {
        "label": "Finance",
        "prompt": "Tu es un analyste financier. Utilise le vocabulaire de la finance, "
                  "détaille tes calculs et rappelle que tes réponses ne sont pas un conseil "
                  "en investissement.",
    },
    "data": {
        "label": "Analyse de données",
        "prompt": "Tu es data analyst. Propose des méthodes statistiques adaptées, du code "
                  "pandas/SQL quand c'est utile et interprète les résultats.",
    },
    "gouvernance": {
        "label": "Gouvernance des données",
        "prompt": "Tu es expert en gouvernance et qualité des données (conformité, RGPD, "
                  "loi 09-08, data lineage, MDM). Propose des plans d'action concrets.",
    },
}

BASE_PROMPT = ("Tu es FlaskGPT, un assistant IA. Réponds dans la langue de l'utilisateur, "
               "en Markdown. Si tu n'es pas sûr, dis-le au lieu d'inventer.")


def build_system_prompt(domain, passages):
    """Assemble : consigne de base + consigne du domaine + extraits des sources."""
    parts = [BASE_PROMPT, DOMAINS.get(domain, DOMAINS["general"])["prompt"]]
    if passages:
        parts.append(
            "Voici des extraits de documents fournis par l'utilisateur. Appuie-toi dessus "
            "en priorité et cite le repère entre crochets (ex. [S1.1]) juste après chaque "
            "affirmation qui en provient. Si les extraits ne contiennent pas la réponse, "
            "dis-le clairement avant de répondre avec tes connaissances générales."
        )
        parts += [f"[{p['ref']}] (source : {p['source_name']})\n{p['text']}" for p in passages]
    return "\n\n".join(parts)


def get_client():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise AppError("Aucune clé API trouvée. Crée un fichier .env avec GROQ_API_KEY=...",
                       500, "missing_api_key")
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=60, max_retries=1)


def translate_error(exc):
    """Fonctionnalité 4 : transforme une exception technique en message lisible."""
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return AppError("La clé API est invalide, expirée ou sans droits suffisants. "
                        "Vérifie GROQ_API_KEY dans ton fichier .env.", 401, "invalid_api_key")
    if isinstance(exc, RateLimitError):
        return AppError("Le quota ou la limite de requêtes de l'API est dépassé. "
                        "Attends un moment ou vérifie ton quota sur la console Groq.",
                        429, "quota_exceeded")
    if isinstance(exc, APITimeoutError):  # à tester AVANT APIConnectionError (sous-classe)
        return AppError("Le service IA met trop de temps à répondre. Réessaie.", 504, "timeout")
    if isinstance(exc, APIConnectionError):
        return AppError("Connexion impossible au service IA. Vérifie ta connexion internet.",
                        503, "connection_error")
    if isinstance(exc, NotFoundError):
        return AppError(f"Modèle introuvable ({MODEL}). Vérifie GROQ_MODEL.", 404, "model_not_found")
    if isinstance(exc, BadRequestError):
        return AppError("Requête refusée par l'API (conversation trop longue ou paramètre invalide).",
                        400, "bad_request")
    if isinstance(exc, APIStatusError):
        return AppError(f"Le service IA a renvoyé une erreur ({exc.status_code}). Réessaie plus tard.",
                        502, "upstream_error")
    return AppError("Une erreur inattendue est survenue.", 500, "unexpected")


def open_stream(messages):
    """Ouvre le flux. Les erreurs de clé/quota/connexion se produisent ICI, avant d'envoyer
    quoi que ce soit au navigateur, ce qui permet de renvoyer un vrai code HTTP."""
    client = get_client()
    try:
        return client.chat.completions.create(model=MODEL, messages=messages, stream=True)
    except Exception as exc:
        raise translate_error(exc) from exc


def complete(messages):
    """Appel simple (sans streaming), utilisé pour le résumé PDF."""
    client = get_client()
    try:
        r = client.chat.completions.create(model=MODEL, messages=messages)
        return (r.choices[0].message.content or "").strip()
    except Exception as exc:
        raise translate_error(exc) from exc


def _usage_from(chunk):
    """Cherche la consommation de tokens dans un fragment (OpenAI: chunk.usage, Groq: x_groq.usage)."""
    for candidate in (getattr(chunk, "usage", None), getattr(chunk, "x_groq", None)):
        if candidate is None:
            continue
        if not isinstance(candidate, dict):
            candidate = candidate.model_dump() if hasattr(candidate, "model_dump") else vars(candidate)
        if isinstance(candidate.get("usage"), dict):
            candidate = candidate["usage"]
        if candidate.get("total_tokens"):
            return {k: int(candidate.get(k) or 0)
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return None


def iter_stream(stream):
    """Produit ("text", fragment) pour chaque morceau, puis ("usage", dict|None) à la fin."""
    usage = None
    for chunk in stream:
        usage = _usage_from(chunk) or usage
        if not chunk.choices:
            continue
        text = chunk.choices[0].delta.content or ""
        if text:
            yield "text", text
    yield "usage", usage
