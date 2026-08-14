"""Module 4 — traduction de l'intention en spécification exécutable.

Couche intermédiaire entre 4.2 (préparation) et 4.3 (exécution). Elle transforme le besoin
exprimé en langage naturel par l'entreprise en une `IntentionAnalysee` validée. **Aucun
calcul n'est effectué ici** : le LLM traduit, il ne compte pas.

L'accès au fournisseur (URL, authentification, forme de la requête, contrainte de sortie
structurée) est isolé dans `llm_client.py` : ce module ne connaît que le prompt, le schéma
fermé de `schema_analyse.py` et la validation qui suit.

**Fonctionne sans clé API.** Si `LLM_API_KEY` est vide, ou si l'appel échoue pour quelque
raison que ce soit, on retombe sur la spécification par défaut de l'objectif prédéfini.
L'analyse n'est interrompue que si aucun objectif n'a été choisi — auquel cas il n'y a
rien à appliquer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.moteur_analyse.llm_client import (
    LLMIndisponible,
    appeler_llm,
    llm_disponible,
)
from app.services.moteur_analyse.schema_analyse import (
    LIBELLE_TYPE,
    IntentionAnalysee,
    SpecificationInvalide,
    schema_json,
    specification_par_defaut,
    valider_reponse,
)

# 60 s et non 30 : la sortie est courte, mais les modèles à raisonnement passent un temps
# variable à réfléchir avant d'écrire — `gemini-3.5-flash` mesuré entre 20 et 27 s sur cet
# appel. À 30 s, une traduction parfaitement valide expirait et retombait sur le repli
# déterministe sans qu'aucun problème réel ne se soit produit.
DELAI_APPEL = 60.0
# Les modèles à raisonnement dépensent une part du budget de sortie avant d'écrire :
# la marge couvre la réflexion en plus de la spécification elle-même.
MAX_TOKENS = 4000


@dataclass
class ResultatTraduction:
    """Sortie de la couche de traduction."""

    intention: IntentionAnalysee
    source: str                 # "llm" | "repli"
    motif_repli: str | None = None   # pourquoi le repli a été utilisé, le cas échéant

    @property
    def reformulation(self) -> str:
        return self.intention.reformulation

    def resume(self) -> dict:
        return {
            "source": self.source,
            "motif_repli": self.motif_repli,
            "reformulation": self.intention.reformulation,
            "specification": self.intention.specification.model_dump(),
            "filtres": [f.model_dump() for f in self.intention.filtres],
        }


# ──────────────────────────────────────────────────────────────────────────────
# Construction du prompt
# ──────────────────────────────────────────────────────────────────────────────

SYSTEME = """Tu traduis le besoin d'analyse d'une entreprise en une spécification exécutable.

Tu ne calcules jamais, tu n'inventes aucun chiffre, tu ne commentes aucun résultat : tu
choisis uniquement l'opération et ses paramètres.

Le moteur ne sait faire que quatre opérations, et rien d'autre :
- classement : ordonner des éléments (produits, clients, régions) selon une mesure.
  « les plus vendus » → ordre decroissant ; « les moins performants », « à mettre en
  promotion », « à déstocker » → ordre croissant.
- prevision : projeter une mesure dans le futur à partir de son historique.
- tendance : dire si une mesure monte ou baisse dans le temps.
- anomalie : repérer les valeurs inhabituelles d'une mesure.

Règles strictes :
- N'utilise que des colonnes présentes dans la liste fournie, à l'orthographe exacte.
- `colonne_temps` doit être une colonne de type date, `mesure` une grandeur mesurable,
  `dimension` une colonne non numérique.
- Une colonne annoncée « identifiant » ou « indicateur oui/non » ne peut jamais servir de
  `mesure` : une clé, un numéro de ligne ou un drapeau ne sont pas des quantités, même
  écrits avec des chiffres. Ils font en revanche de bonnes `dimension`.
- Si le besoin exprimé et l'objectif choisi divergent, le besoin exprimé prime.
- `reformulation` est obligatoire : une phrase en français, **au vouvoiement** (« vos
  produits », jamais « tes produits » — toute l'application vouvoie), qui redit à
  l'entreprise ce que tu as compris. Elle lui sert à repérer un contresens.
  Exemple : « Classer vos produits par chiffre d'affaires décroissant, les 10 premiers. »
  **220 caractères au maximum** — la validation coupe à 300, cette marge évite qu'un
  débordement ordinaire fasse rejeter toute la réponse."""


def _decrire_colonnes(profil: dict) -> str:
    lignes = []
    for table, infos in profil.items():
        for col in infos.get("colonnes", []):
            if col["est_date"]:
                nature = "date"
            elif col.get("est_identifiant"):
                # Annoncé pour ce qu'il est : le modèle ne doit pas le confondre avec une
                # grandeur sous prétexte qu'il est numérique. La validation le rejetterait
                # de toute façon — autant ne pas provoquer un repli évitable.
                nature = "identifiant — utilisable comme dimension, jamais comme mesure"
            elif col.get("est_booleen"):
                nature = "indicateur oui/non — utilisable comme dimension, jamais comme mesure"
            elif col["est_numerique"]:
                nature = "numérique"
            else:
                nature = "texte"
            lignes.append(f"- {col['nom']} ({nature}, table « {table} »)")
    return "\n".join(lignes)


def construire_prompt(besoin: str, objectif: str | None, profil: dict) -> str:
    morceaux = ["Colonnes disponibles :", _decrire_colonnes(profil), ""]
    if objectif and objectif in _OBJECTIF_LIBELLES:
        morceaux.append(f"Objectif prédéfini choisi : {_OBJECTIF_LIBELLES[objectif]}")
    if besoin:
        morceaux.append(f"Besoin exprimé par l'entreprise : « {besoin} »")
    if not besoin and objectif:
        morceaux.append(
            "Aucun besoin n'a été exprimé : applique l'objectif prédéfini tel quel."
        )
    morceaux.append("")
    morceaux.append("Produis la spécification correspondante.")
    return "\n".join(morceaux)


_OBJECTIF_LIBELLES = {
    "comparaison_classement": "Comparer et classer",
    "prevision_evolution": "Prévoir une évolution",
    "prevision_tendance": "Détecter une tendance",
    "detection_anomalie": "Identifier une anomalie",
}


# ──────────────────────────────────────────────────────────────────────────────
# Appel LLM
# ──────────────────────────────────────────────────────────────────────────────

def _appeler_llm(besoin: str, objectif: str | None, profil: dict) -> dict:
    """Un appel, une réponse JSON brute. Lève `SpecificationInvalide` sur échec.

    Tout ce qui dépend du fournisseur — URL, authentification, forme de la requête,
    contrainte de sortie structurée — est dans `llm_client`. Ici il ne reste que le
    prompt, le schéma et la traduction de l'échec dans notre vocabulaire d'erreurs.
    """
    try:
        return appeler_llm(
            systeme=SYSTEME,
            invite=construire_prompt(besoin, objectif, profil),
            schema=schema_json(),
            max_tokens=MAX_TOKENS,
            delai=DELAI_APPEL,
        )
    except LLMIndisponible as exc:
        raise SpecificationInvalide(exc.message, exc.details)


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────────────

def traduire_intention(besoin: str, objectif: str | None, profil: dict) -> ResultatTraduction:
    """Traduit un besoin en spécification validée.

    Ordre : LLM si disponible → validation stricte → repli déterministe si l'un des deux
    échoue. Ne lève que si le repli est lui aussi impossible (aucun objectif prédéfini).
    """
    besoin = (besoin or "").strip()
    if not besoin and not objectif:
        raise SpecificationInvalide(
            "Cette configuration ne précise ni objectif ni besoin : "
            "indiquez ce que vous souhaitez savoir pour lancer l'analyse."
        )

    if not llm_disponible():
        return _replier(objectif, profil, "aucune clé d'API configurée (LLM_API_KEY)")

    try:
        brut = _appeler_llm(besoin, objectif, profil)
        intention = valider_reponse(brut, profil)
        return ResultatTraduction(intention=intention, source="llm")
    except SpecificationInvalide as exc:
        return _replier(objectif, profil, exc.message_complet())
    except Exception as exc:  # filet : aucune exception brute ne doit remonter
        return _replier(objectif, profil, f"erreur inattendue ({exc})")


def _replier(objectif: str | None, profil: dict, motif: str) -> ResultatTraduction:
    """Applique la spécification par défaut de l'objectif prédéfini.

    Sans objectif choisi il n'y a rien à appliquer : on interrompt avec un message qui
    dit à l'entreprise quoi faire.
    """
    if not objectif:
        # Message actionnable : l'entreprise doit savoir quoi faire, pas seulement que
        # ça ne marche pas. Son besoin est conservé — c'est le point rassurant à dire.
        raise SpecificationInvalide(
            "Votre besoin n'a pas pu être interprété automatiquement. "
            "Modifiez cette configuration et choisissez un objectif prédéfini en complément : "
            "votre description est conservée, et l'analyse redevient réalisable.",
            [motif],
        )
    intention = specification_par_defaut(objectif, profil)
    return ResultatTraduction(intention=intention, source="repli", motif_repli=motif)


def libelle_operation(intention: IntentionAnalysee) -> str:
    """Libellé lisible du type d'analyse retenu."""
    return LIBELLE_TYPE.get(intention.specification.type_analyse, intention.specification.type_analyse)
