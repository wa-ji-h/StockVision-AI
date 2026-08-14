"""Module 4 — tâche 4.5 : restitution des résultats en langage naturel.

Dernière couche de la chaîne : elle transforme les chiffres produits par 4.4 en une
interprétation compréhensible par l'entreprise. **Le LLM commente, il ne calcule pas** —
il ne reçoit aucune donnée à agréger, seulement le résultat déjà calculé.

Trois garanties structurantes :

1. **Aucune donnée brute ne franchit la frontière.** `charge_utile()` est une liste
   blanche : seuls les agrégats, indicateurs et métadonnées du résultat sont transmis.
   Aucune ligne de la source, jamais, même tronquée. Ce qui n'est pas listé ici
   n'est pas envoyé — c'est l'inverse d'un filtrage par exclusion, qui laisse
   toujours passer ce qu'on a oublié d'exclure.

2. **La réponse est structurée, donc enregistrable sans relecture humaine.**
   `InterpretationRedigee` est un schéma fermé (`extra="forbid"`) : la sortie du modèle
   est validée avant d'atteindre la base, exactement comme la spécification en 4.3.

3. **L'indisponibilité du service ne casse rien.** Aucun repli rédigé n'est fabriqué :
   les chiffres restent consultables, seule l'interprétation manque, et le motif est
   conservé. C'est une différence assumée avec 4.3, qui replie sur une spécification
   par défaut — là-bas le repli permet de *calculer*, ici il n'y aurait rien à sauver :
   une interprétation générée par gabarit serait indiscernable d'une vraie lecture.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.moteur_analyse.execution import ResultatExecution
from app.services.moteur_analyse.llm_client import (
    LLMIndisponible,
    appeler_llm,
    llm_disponible,
)
from app.services.moteur_analyse.schema_analyse import LIBELLE_TYPE

_log = logging.getLogger("stockvision.moteur_analyse")

DELAI_APPEL = 60.0          # rédaction plus longue qu'une traduction, mais bornée
# Marge pour les modèles à raisonnement, qui puisent dans le budget de sortie avant
# d'écrire : sans elle, la réponse revient vide avec un motif d'arrêt « MAX_TOKENS ».
MAX_TOKENS = 4000

# Nombre d'agrégats transmis au modèle. Plafond volontaire : au-delà, on n'améliore pas
# la lecture, on rapproche seulement la charge utile d'un export de données.
MAX_ELEMENTS_TRANSMIS = 5

NiveauConfiance = Literal["eleve", "modere", "faible"]

LIBELLE_CONFIANCE: dict[str, str] = {
    "eleve": "Élevée",
    "modere": "Modérée",
    "faible": "Faible",
}


# ──────────────────────────────────────────────────────────────────────────────
# Schéma fermé de la réponse rédigée
# ──────────────────────────────────────────────────────────────────────────────

class InterpretationRedigee(BaseModel):
    """Format préétabli de l'interprétation. Fermé : aucune clé inventée n'est acceptée."""

    model_config = ConfigDict(extra="forbid")

    # La réponse à la question posée, en une phrase. C'est elle qui s'affiche en tête.
    synthese: str = Field(min_length=15, max_length=300)
    # Le raisonnement : ce que disent les chiffres, et à quel point ils le disent.
    lecture: str = Field(min_length=30, max_length=900)
    # 2 à 4 constats saillants, un par ligne.
    points_cles: list[str] = Field(min_length=1, max_length=4)
    # Ce que l'entreprise peut faire de ce résultat. Jamais un chiffre inventé.
    recommandation: str = Field(min_length=10, max_length=400)
    # Ce que cette analyse ne permet PAS de conclure. Obligatoire : sans cette limite,
    # une incertitude se lit comme un fait.
    limite: str = Field(min_length=10, max_length=400)
    niveau_confiance: NiveauConfiance


class InterpretationInvalide(Exception):
    """Réponse du service non exploitable. Porte un message lisible."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []

    def message_complet(self) -> str:
        if not self.details:
            return self.message
        return f"{self.message} ({' ; '.join(self.details)})"


class FormatInvalide(InterpretationInvalide):
    """La réponse est arrivée mais ne respecte pas le schéma.

    Distincte des échecs de transport : une panne réseau ne se corrige pas en redemandant
    poliment, un dépassement de longueur si. C'est cette distinction qui autorise une
    seconde tentative ciblée plutôt qu'un réessai aveugle.
    """

    def __init__(self, message: str, details: list[str] | None = None,
                 brut: dict | None = None, corrections: list[str] | None = None):
        super().__init__(message, details)
        self.brut = brut or {}            # la réponse rejetée, pour la journaliser
        self.corrections = corrections or []   # consignes à renvoyer au modèle


# ──────────────────────────────────────────────────────────────────────────────
# Longueurs — une seule source : le modèle Pydantic lui-même
# ──────────────────────────────────────────────────────────────────────────────
#
# Gemini accepte `maxLength` dans le schéma mais ne l'applique pas. La consigne doit donc
# être portée par le prompt — et si le prompt annonçait la limite exacte, le moindre
# débordement ferait tout rejeter. On annonce donc une **cible plus basse que la limite
# réelle** : le dépassement ordinaire tombe dans la marge au lieu de coûter
# l'interprétation entière.

MARGE_LONGUEUR = 0.75      # cible annoncée = 75 % de la limite réellement validée


def _limites_max() -> dict[str, int]:
    """Longueurs maximales déclarées sur `InterpretationRedigee`, lues, jamais recopiées."""
    limites: dict[str, int] = {}
    for nom, champ in InterpretationRedigee.model_fields.items():
        for contrainte in champ.metadata:
            maxi = getattr(contrainte, "max_length", None)
            if maxi is not None:
                limites[nom] = int(maxi)
    return limites


LIMITES_MAX = _limites_max()
CIBLES_LONGUEUR = {nom: max(1, int(maxi * MARGE_LONGUEUR)) for nom, maxi in LIMITES_MAX.items()}
# `points_cles` borne un nombre d'éléments, pas des caractères : l'unité change le libellé.
_CHAMPS_LISTE = {"points_cles"}


def _consignes_longueur(cibles: dict[str, int]) -> str:
    lignes = []
    for nom, cible in cibles.items():
        unite = "éléments" if nom in _CHAMPS_LISTE else "caractères"
        lignes.append(f"- `{nom}` : {cible} {unite} au maximum.")
    return "\n".join(lignes)


@dataclass
class ResultatInterpretation:
    """Sortie de la couche. `disponible` faux ⇒ les chiffres restent, le texte manque."""

    disponible: bool
    interpretation: InterpretationRedigee | None = None
    motif_indisponible: str | None = None
    # Trace des garde-fous appliqués après coup (ex. confiance revue à la baisse).
    ajustements: list[str] = field(default_factory=list)

    @property
    def synthese(self) -> str | None:
        return self.interpretation.synthese if self.interpretation else None

    def resume(self) -> dict:
        return {
            "disponible": self.disponible,
            "motif_indisponible": self.motif_indisponible,
            "ajustements": self.ajustements,
            "interpretation": (
                self.interpretation.model_dump() if self.interpretation else None
            ),
        }


def schema_reponse() -> dict:
    """Schéma JSON de la réponse attendue.

    Rendu tel quel : c'est `llm_client` qui l'adapte aux restrictions du fournisseur
    actif, parce que ces restrictions changent avec lui. Pydantic revalide de toute
    façon à la réception — c'est ce qui rend l'adaptation inoffensive.
    """
    return InterpretationRedigee.model_json_schema()


# ──────────────────────────────────────────────────────────────────────────────
# Charge utile — la frontière entre les données et le service externe
# ──────────────────────────────────────────────────────────────────────────────

def charge_utile(resultat: ResultatExecution) -> dict:
    """Ce qui est transmis au modèle, et rien d'autre.

    Liste blanche stricte. Les séries temporelles complètes, les lignes sources et les
    observations détaillées ne sortent pas : le modèle n'en a pas besoin pour commenter
    un chiffre, et les lui envoyer reviendrait à externaliser les données de l'entreprise.

    Les libellés d'agrégats (nom de produit en tête d'un classement) sont transmis, mais
    plafonnés à `MAX_ELEMENTS_TRANSMIS` : ce sont des résultats de regroupement, pas des
    enregistrements — sans eux, l'interprétation d'un classement ne pourrait rien nommer.
    """
    fiabilite = resultat.fiabilite or {}
    utile: dict = {
        "type_analyse": resultat.type_analyse,
        "operation": LIBELLE_TYPE.get(resultat.type_analyse, resultat.type_analyse),
        "valeur_analyse": resultat.valeur_analyse,
        "valeur_prevue": resultat.valeur_prevue,
        "unite": resultat.unite,
        "modele_applique": resultat.modele_libelle,
        "fiabilite": {
            "niveau": fiabilite.get("code"),
            "points": fiabilite.get("points"),
            "phrase": fiabilite.get("phrase"),
        },
        # Les indicateurs structurés de 4.4, dont `conclusif` — le cœur de la lecture.
        "indicateurs": resultat.indicateurs,
        # Criticité déduite par 4.6 : un agrégat, comme le reste. Le modèle ne la calcule
        # pas — il en tient compte pour doser son propos.
        "criticite": resultat.criticite,
        "avertissements": resultat.avertissements,
    }

    if resultat.intervalle_bas is not None:
        utile["intervalle"] = {
            "bas": resultat.intervalle_bas,
            "haut": resultat.intervalle_haut,
            # « modele » ou « residus » : l'origine de l'intervalle n'est pas un détail,
            # un intervalle estimé sur les résidus ne se commente pas comme un natif.
            "methode": resultat.methode_intervalle,
        }

    if resultat.elements_classes:
        utile["elements_en_tete"] = resultat.elements_classes[:MAX_ELEMENTS_TRANSMIS]
        utile["nb_elements_total"] = len(resultat.elements_classes)

    if resultat.observations_aberrantes:
        # Le nombre et l'ampleur suffisent à commenter : le détail des observations reste
        # en base, consultable par l'entreprise, non transmis.
        ecarts = [
            abs(o["ecart_zscore"]) for o in resultat.observations_aberrantes
            if isinstance(o.get("ecart_zscore"), (int, float))
        ]
        utile["anomalies"] = {
            "nombre": len(resultat.observations_aberrantes),
            "ecart_max": max(ecarts) if ecarts else None,
        }

    if resultat.serie_historique:
        # Cardinalité et bornes temporelles, jamais les points eux-mêmes.
        utile["historique"] = {
            "nb_points": len(resultat.serie_historique),
            "debut": resultat.serie_historique[0].get("date"),
            "fin": resultat.serie_historique[-1].get("date"),
        }

    return utile


# ──────────────────────────────────────────────────────────────────────────────
# Construction de la requête
# ──────────────────────────────────────────────────────────────────────────────

SYSTEME = """Tu restitues à une entreprise le résultat d'une analyse déjà calculée.

Tu ne calcules rien, tu ne recalcules rien, tu n'inventes aucun chiffre : tous les
nombres que tu cites doivent provenir des résultats fournis, à l'identique.

Tu écris en français, **au vouvoiement** (« vos ventes », jamais « tes ventes »), dans un
langage d'entreprise : pas de vocabulaire statistique non expliqué. Si tu mentionnes le modèle
employé, dis à quoi il sert, pas comment il fonctionne.

Règle décisive — l'indicateur `conclusif` :
- `conclusif: false` signifie que le résultat NE TRANCHE PAS. Tu ne dois alors présenter
  aucune hausse, aucune baisse ni aucun classement comme établi. Dis explicitement que
  les données ne permettent pas de conclure, et pourquoi (intervalle trop large, droite
  qui explique mal les points, élément unique, aucune anomalie détectée).
- Un intervalle de prévision qui contient la valeur de départ veut dire que le modèle
  ne distingue pas une hausse d'une baisse. Ne commente jamais la variation seule.

`niveau_confiance` : `eleve` seulement si `conclusif` est vrai ET la fiabilité est
« bonne » ; `faible` dès que `conclusif` est faux ; `modere` sinon.

`limite` est obligatoire : dis ce que cette analyse ne permet pas de conclure. Une
analyse honnête sur ses limites vaut mieux qu'une analyse affirmative et fausse.

Longueurs à respecter strictement. Un seul dépassement fait rejeter la réponse entière et
l'entreprise se retrouve sans interprétation. Compte les caractères avant de répondre, et
préfère une phrase de moins à une phrase de trop :
{consignes}

`synthese` tient en une seule phrase : la réponse à la question posée, rien de plus."""

SYSTEME = SYSTEME.format(consignes=_consignes_longueur(CIBLES_LONGUEUR))


def construire_prompt(
    resultat: ResultatExecution,
    objectif_libelle: str | None,
    besoin: str | None,
    reformulation: str | None,
) -> str:
    """Assemble la requête à partir du résultat, de l'objectif et du besoin exprimé.

    Construction automatique, sans intervention : la demande d'origine est rappelée au
    modèle pour qu'il réponde à *cette* question, pas à celle que suggèrent les chiffres.
    """
    morceaux: list[str] = ["Demande initiale de l'entreprise"]
    if objectif_libelle:
        morceaux.append(f"- Objectif choisi : {objectif_libelle}")
    if besoin:
        morceaux.append(f"- Besoin exprimé : « {besoin} »")
    if reformulation:
        morceaux.append(f"- Compris comme : « {reformulation} »")
    if not objectif_libelle and not besoin:
        morceaux.append("- Aucune précision fournie : commente le résultat tel quel.")

    morceaux += [
        "",
        "Résultats calculés (ne recalcule rien, cite ces valeurs telles quelles) :",
        json.dumps(charge_utile(resultat), ensure_ascii=False, indent=2, default=str),
        "",
        "Rédige l'interprétation destinée à l'entreprise.",
    ]
    return "\n".join(morceaux)


# ──────────────────────────────────────────────────────────────────────────────
# Appel LLM
# ──────────────────────────────────────────────────────────────────────────────

def _appeler_llm(prompt: str) -> dict:
    """Un appel, une réponse JSON brute. Lève `InterpretationInvalide` sur échec.

    Comme en 4.3, tout ce qui dépend du fournisseur vit dans `llm_client` : ce module ne
    garde que la charge utile, le schéma et la traduction de l'échec.
    """
    try:
        return appeler_llm(
            systeme=SYSTEME,
            invite=prompt,
            schema=schema_reponse(),
            max_tokens=MAX_TOKENS,
            delai=DELAI_APPEL,
        )
    except LLMIndisponible as exc:
        raise InterpretationInvalide(exc.message, exc.details)


def _mesurer(brut: dict, champ: str) -> str:
    """Taille réellement produite pour un champ, dans son unité."""
    valeur = brut.get(champ)
    if isinstance(valeur, str):
        return f"{len(valeur)} caractères"
    if isinstance(valeur, list):
        return f"{len(valeur)} éléments"
    return "taille inconnue"


def valider_reponse(brut: dict) -> InterpretationRedigee:
    """Valide la forme. Les bornes que le fournisseur n'applique pas sont imposées ici.

    En cas d'échec, le détail dit **quel champ, de combien** : sans ces deux nombres, ni
    la trace en base ni la seconde tentative ne sont exploitables.
    """
    try:
        return InterpretationRedigee.model_validate(brut)
    except ValidationError as exc:
        details, corrections = [], []
        for e in exc.errors()[:5]:
            champ = ".".join(str(p) for p in e["loc"]) or "racine"
            racine = str(e["loc"][0]) if e["loc"] else ""
            maxi = LIMITES_MAX.get(racine)
            if maxi is not None and e["type"] in ("string_too_long", "too_long"):
                produit = _mesurer(brut, racine)
                details.append(f"{champ} : {produit} pour {maxi} au maximum")
                corrections.append(
                    f"- `{racine}` faisait {produit} : réécris-le en "
                    f"{CIBLES_LONGUEUR.get(racine, maxi)} au maximum."
                )
            else:
                details.append(f"{champ} : {e['msg']}")
                corrections.append(f"- `{champ}` : {e['msg']}.")
        raise FormatInvalide(
            "L'interprétation produite ne respecte pas le format attendu.",
            details, brut=brut, corrections=corrections,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Garde-fou : la confiance affichée ne peut pas dépasser ce que les chiffres portent
# ──────────────────────────────────────────────────────────────────────────────

def confiance_maximale(resultat: ResultatExecution) -> str:
    """Plafond de confiance déduit des seuls indicateurs, sans le texte du modèle."""
    if resultat.indicateurs.get("conclusif") is False:
        return "faible"
    if (resultat.fiabilite or {}).get("code") == "bonne":
        return "eleve"
    return "modere"


_RANG_CONFIANCE = {"faible": 0, "modere": 1, "eleve": 2}


def _plafonner_confiance(
    interpretation: InterpretationRedigee, resultat: ResultatExecution
) -> list[str]:
    """Rabat `niveau_confiance` sur le plafond calculé, si le modèle l'a surestimé.

    Le prompt énonce déjà la règle ; ce contrôle la rend **vérifiée** plutôt que
    demandée. Même logique qu'en 4.3 : ce qui compte n'est pas ce qu'on a demandé au
    modèle, c'est ce qu'on accepte de lui.
    """
    plafond = confiance_maximale(resultat)
    if _RANG_CONFIANCE[interpretation.niveau_confiance] <= _RANG_CONFIANCE[plafond]:
        return []
    annonce = interpretation.niveau_confiance
    interpretation.niveau_confiance = plafond          # type: ignore[assignment]
    return [
        f"confiance ramenée de « {LIBELLE_CONFIANCE[annonce]} » à "
        f"« {LIBELLE_CONFIANCE[plafond]} » : les indicateurs ne soutiennent pas davantage"
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────────────

def interpreter_resultat(
    resultat: ResultatExecution,
    objectif_libelle: str | None = None,
    besoin: str | None = None,
    reformulation: str | None = None,
) -> ResultatInterpretation:
    """Rédige l'interprétation d'un résultat calculé. **Ne lève jamais.**

    Toute défaillance (clé absente, délai, refus, JSON non conforme) rend un
    `ResultatInterpretation` indisponible porteur du motif — les chiffres, eux, sont
    déjà en base et restent consultables.
    """
    if not llm_disponible():
        return ResultatInterpretation(
            disponible=False,
            motif_indisponible="aucune clé d'API configurée (LLM_API_KEY)",
        )

    reprises: list[str] = []
    try:
        prompt = construire_prompt(resultat, objectif_libelle, besoin, reformulation)
        try:
            interpretation = valider_reponse(_appeler_llm(prompt))
        except FormatInvalide as premier:
            # La réponse est arrivée, elle est simplement hors format. On la journalise
            # — sinon le contenu rejeté est perdu et le défaut indiagnosticable — puis on
            # redemande **une seule fois**, en nommant précisément ce qui a débordé.
            _log.warning(
                "[interpretation] format rejeté (%s) — nouvelle tentative. Contenu rejeté : %s",
                " ; ".join(premier.details), json.dumps(premier.brut, ensure_ascii=False)[:1500],
            )
            reprises.append(f"1re réponse rejetée — {' ; '.join(premier.details)}")
            interpretation = valider_reponse(_appeler_llm(_prompt_correctif(prompt, premier)))
            _log.info("[interpretation] seconde tentative acceptée")
    except FormatInvalide as second:
        # Deux échecs de format d'affilée : on renonce, en gardant la trace du contenu.
        _log.warning(
            "[interpretation] format rejeté deux fois (%s). Contenu rejeté : %s",
            " ; ".join(second.details), json.dumps(second.brut, ensure_ascii=False)[:1500],
        )
        return ResultatInterpretation(
            disponible=False,
            motif_indisponible=f"{second.message_complet()} — après une seconde tentative.",
        )
    except InterpretationInvalide as exc:
        return ResultatInterpretation(disponible=False, motif_indisponible=exc.message_complet())
    except Exception as exc:  # filet : aucune exception brute ne doit remonter
        return ResultatInterpretation(
            disponible=False, motif_indisponible=f"erreur inattendue ({exc})"
        )

    ajustements = reprises + _plafonner_confiance(interpretation, resultat)
    return ResultatInterpretation(
        disponible=True, interpretation=interpretation, ajustements=ajustements
    )


def _prompt_correctif(prompt: str, echec: FormatInvalide) -> str:
    """Reprend la demande en nommant ce qui a débordé, et de combien.

    Une consigne générale a déjà échoué une fois : la répéter n'apporterait rien. Ce
    second appel dit le champ, la taille produite et la taille attendue.
    """
    return "\n".join([
        prompt,
        "",
        "ATTENTION — ta réponse précédente a été REJETÉE, l'entreprise n'a rien reçu.",
        "Motif :",
        *echec.corrections,
        "",
        "Reprends la même analyse, avec le même fond, mais plus court sur les champs "
        "signalés. Coupe des phrases entières plutôt que de tronquer la dernière.",
    ])


def interpreter_et_stocker(db, config, resultat: ResultatExecution) -> ResultatInterpretation:
    """Interprète puis persiste sur la ligne `ResultatAnalyse` produite par 4.4.

    Ne lève jamais. Un échec est journalisé et écrit dans `interpretation_erreur`,
    colonne dédiée de la ligne de résultat — la règle « chaque étape écrit dans sa
    propre colonne » vaut ici aussi.
    """
    from app.database.models.resultat_analyse import ResultatAnalyse

    cfg_id = config.id_configuration
    _log.info("[interpretation] cfg %s : début (%s)", cfg_id, resultat.type_analyse)

    sortie = interpreter_resultat(
        resultat,
        objectif_libelle=LIBELLE_TYPE.get(resultat.type_analyse),
        besoin=getattr(config, "besoin", None),
        reformulation=getattr(config, "intention_reformulee", None),
    )

    ligne = None
    if resultat.id_resultat is not None:
        ligne = db.get(ResultatAnalyse, resultat.id_resultat)
    if ligne is None:
        _log.warning("[interpretation] cfg %s : ligne de résultat introuvable, rien à écrire", cfg_id)
        return sortie

    if sortie.disponible and sortie.interpretation:
        ligne.interpretation = sortie.interpretation.synthese
        ligne.interpretation_json = json.dumps(sortie.resume(), ensure_ascii=False)
        ligne.interpretation_source = "llm"
        ligne.interpretation_erreur = None
        _log.info(
            "[interpretation] cfg %s : OK (confiance=%s%s)",
            cfg_id, sortie.interpretation.niveau_confiance,
            ", ajustée" if sortie.ajustements else "",
        )
    else:
        # Les chiffres restent. Seule la rédaction manque, et on dit pourquoi.
        ligne.interpretation = None
        ligne.interpretation_json = None
        ligne.interpretation_source = "indisponible"
        ligne.interpretation_erreur = sortie.motif_indisponible
        _log.warning(
            "[interpretation] cfg %s : indisponible — %s", cfg_id, sortie.motif_indisponible
        )

    db.commit()
    return sortie
