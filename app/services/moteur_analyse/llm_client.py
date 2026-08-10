"""Module 4 — couche d'accès au fournisseur de modèle de langage.

**Tout ce qui est propre à un fournisseur vit ici, et nulle part ailleurs.** URL,
en-têtes d'authentification, forme de la requête, extraction du texte, détection d'un
refus, adaptation du schéma JSON : changer de fournisseur ne doit toucher que ce fichier.
`traduction.py` (4.3) et `interpretation.py` (4.5) ne connaissent que `appeler_llm()`.

Ce qui n'est **pas** ici, et n'y sera jamais : le schéma fermé et la double validation
Pydantic. Ce sont nos garanties, elles ne dépendent d'aucun fournisseur — et l'expérience
montre qu'elles servent : Gemini a renvoyé `type_analyse: "classification"`, hors du
vocabulaire fermé, sur une requête pourtant contrainte par `responseJsonSchema`. La
contrainte du fournisseur est une aide, jamais la garantie.

Appel HTTP direct via `httpx`, conformément aux règles du projet : aucun SDK.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx

from app.core.config import settings


class LLMIndisponible(Exception):
    """Échec d'appel, porteur d'un message lisible et de détails techniques."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []


# ──────────────────────────────────────────────────────────────────────────────
# Adaptation du schéma — chaque API impose ses propres restrictions
# ──────────────────────────────────────────────────────────────────────────────

_CONTRAINTES_REFUSEES_ANTHROPIC = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems",
)


def _adapter_schema_anthropic(noeud):
    """Anthropic refuse les bornes et exige `additionalProperties: false` + `required` complet.

    Les bornes retirées ici sont **revalidées par Pydantic** à la réception : le modèle
    est cadré au mieux de ce que l'API permet, et nous restons juges.
    """
    if isinstance(noeud, dict):
        propre = {
            cle: _adapter_schema_anthropic(val)
            for cle, val in noeud.items()
            if cle not in _CONTRAINTES_REFUSEES_ANTHROPIC
        }
        if propre.get("type") == "object" and "properties" in propre:
            propre["additionalProperties"] = False
            propre["required"] = list(propre["properties"].keys())
        return propre
    if isinstance(noeud, list):
        return [_adapter_schema_anthropic(v) for v in noeud]
    return noeud


def _adapter_schema_gemini(schema: dict) -> dict:
    """Gemini accepte le schéma Pydantic tel quel via `responseJsonSchema`.

    Vérifié contre l'API : `$defs`, `$ref`, `anyOf`, `const`, `additionalProperties` et
    les bornes de longueur passent sans être retirés. C'est `responseSchema` (l'autre
    champ, calqué sur OpenAPI) qui rejette `$defs`/`$ref` par un 400 — nos unions
    discriminées en produisent, d'où le choix de `responseJsonSchema`.
    """
    return schema


# ──────────────────────────────────────────────────────────────────────────────
# Construction de la requête et lecture de la réponse, par fournisseur
# ──────────────────────────────────────────────────────────────────────────────

VERSION_API_ANTHROPIC = "2023-06-01"

# Motifs d'arrêt qui signalent un refus des garde-fous, non une erreur technique.
_ARRETS_REFUS_GEMINI = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}


def _requete_anthropic(systeme: str, invite: str, schema: dict, max_tokens: int) -> tuple[str, dict, dict]:
    corps = {
        "model": settings.LLM_MODEL,
        "max_tokens": max_tokens,
        "system": systeme,
        "messages": [{"role": "user", "content": invite}],
        "output_config": {
            "format": {"type": "json_schema", "schema": _adapter_schema_anthropic(schema)},
            "effort": "medium",
        },
    }
    entetes = {
        "x-api-key": settings.LLM_API_KEY,
        "anthropic-version": VERSION_API_ANTHROPIC,
        "content-type": "application/json",
    }
    return f"{_base_url()}/v1/messages", entetes, corps


def _lire_anthropic(donnees: dict) -> str:
    if donnees.get("stop_reason") == "refusal":
        raise LLMIndisponible("Le service a refusé de traiter cette demande.")
    return next(
        (b.get("text", "") for b in donnees.get("content", []) if b.get("type") == "text"),
        "",
    )


def _requete_gemini(systeme: str, invite: str, schema: dict, max_tokens: int) -> tuple[str, dict, dict]:
    corps = {
        "contents": [{"role": "user", "parts": [{"text": invite}]}],
        # Gemini sépare la consigne système du tour de conversation, contrairement au
        # champ `system` d'Anthropic : même intention, autre emplacement.
        "systemInstruction": {"parts": [{"text": systeme}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": _adapter_schema_gemini(schema),
            "maxOutputTokens": max_tokens,
        },
    }
    entetes = {"x-goog-api-key": settings.LLM_API_KEY, "content-type": "application/json"}
    url = f"{_base_url()}/v1beta/models/{settings.LLM_MODEL}:generateContent"
    return url, entetes, corps


def _lire_gemini(donnees: dict) -> str:
    # Requête bloquée avant génération : le motif est au niveau de la demande.
    blocage = (donnees.get("promptFeedback") or {}).get("blockReason")
    if blocage:
        raise LLMIndisponible(f"Le service a refusé de traiter cette demande ({blocage}).")

    candidats = donnees.get("candidates") or []
    if not candidats:
        raise LLMIndisponible("Le service n'a renvoyé aucune réponse.")

    candidat = candidats[0]
    arret = candidat.get("finishReason")
    if arret in _ARRETS_REFUS_GEMINI:
        raise LLMIndisponible(f"Le service a refusé de produire cette réponse ({arret}).")

    texte = "".join(
        p.get("text", "") for p in (candidat.get("content") or {}).get("parts", [])
    )
    if not texte.strip() and arret == "MAX_TOKENS":
        # Les modèles à raisonnement consomment le budget de sortie avant d'écrire :
        # sans ce message, l'échec ressemblerait à une réponse vide inexpliquée.
        raise LLMIndisponible(
            "Le service a épuisé son budget de réponse avant d'écrire quoi que ce soit. "
            "Augmentez la limite de tokens de sortie."
        )
    return texte


def _erreur_gemini(reponse: httpx.Response) -> str:
    try:
        return (reponse.json().get("error") or {}).get("message", "")
    except Exception:
        return reponse.text[:200]


def _erreur_anthropic(reponse: httpx.Response) -> str:
    try:
        return (reponse.json().get("error") or {}).get("message", "")
    except Exception:
        return reponse.text[:200]


# nom → (constructeur de requête, lecteur de réponse, lecteur d'erreur, URL par défaut)
FOURNISSEURS: dict[str, dict[str, Callable | str]] = {
    "gemini": {
        "requete": _requete_gemini,
        "lire": _lire_gemini,
        "erreur": _erreur_gemini,
        "base_url": "https://generativelanguage.googleapis.com",
        "libelle": "Google AI Studio (Gemini)",
    },
    "anthropic": {
        "requete": _requete_anthropic,
        "lire": _lire_anthropic,
        "erreur": _erreur_anthropic,
        "base_url": "https://api.anthropic.com",
        "libelle": "Anthropic",
    },
}

FOURNISSEUR_DEFAUT = "gemini"


def fournisseur() -> dict:
    """Configuration du fournisseur actif. Un nom inconnu retombe sur le défaut."""
    nom = (getattr(settings, "LLM_PROVIDER", "") or FOURNISSEUR_DEFAUT).strip().lower()
    return FOURNISSEURS.get(nom, FOURNISSEURS[FOURNISSEUR_DEFAUT])


def nom_fournisseur() -> str:
    nom = (getattr(settings, "LLM_PROVIDER", "") or FOURNISSEUR_DEFAUT).strip().lower()
    return nom if nom in FOURNISSEURS else FOURNISSEUR_DEFAUT


def _base_url() -> str:
    """URL configurée si elle existe, sinon celle du fournisseur actif."""
    configuree = (getattr(settings, "LLM_BASE_URL", "") or "").strip()
    return (configuree or fournisseur()["base_url"]).rstrip("/")


def llm_disponible() -> bool:
    """Une clé suffit : sans elle, les appelants basculent sur leur repli déterministe."""
    return bool(settings.LLM_API_KEY)


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée unique
# ──────────────────────────────────────────────────────────────────────────────

def appeler_llm(
    systeme: str,
    invite: str,
    schema: dict,
    max_tokens: int,
    delai: float,
) -> dict:
    """Un appel contraint par `schema`, une réponse JSON déjà décodée.

    Lève `LLMIndisponible` pour tout échec — réseau, statut HTTP, refus, JSON illisible.
    La validation Pydantic du contenu reste à la charge de l'appelant : ce module ne sait
    rien de ce qu'il transporte.
    """
    config = fournisseur()
    url, entetes, corps = config["requete"](systeme, invite, schema, max_tokens)

    try:
        reponse = httpx.post(url, json=corps, headers=entetes, timeout=delai)
    except httpx.TimeoutException:
        raise LLMIndisponible("Le service d'interprétation n'a pas répondu à temps.")
    except httpx.HTTPError as exc:
        raise LLMIndisponible(f"Service d'interprétation injoignable : {exc}")

    if reponse.status_code != 200:
        detail = config["erreur"](reponse)
        raise LLMIndisponible(
            f"Le service d'interprétation a renvoyé une erreur ({reponse.status_code}).",
            [detail] if detail else None,
        )

    try:
        donnees = reponse.json()
    except Exception:
        raise LLMIndisponible("La réponse du service n'est pas du JSON.")

    texte = config["lire"](donnees)
    if not texte.strip():
        raise LLMIndisponible("Le service d'interprétation a renvoyé une réponse vide.")

    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        raise LLMIndisponible("La réponse du service d'interprétation n'est pas du JSON.")
