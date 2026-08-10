"""Module 4 — vocabulaire des opérations et schéma fermé de la spécification d'analyse.

**Contrat de rôles, non négociable :**
- Le LLM *traduit* une intention en spécification. Il ne calcule jamais.
- Le moteur statistique *exécute* la spécification. Il ne lit jamais de texte libre.
- Pydantic garantit que le moteur ne reçoit que des instructions exécutables.

Le schéma est **fermé** (`extra="forbid"` partout, énumérations `Literal`) : toute clé
inventée ou toute opération inconnue fait échouer la validation, et rien n'est exécuté.

Deux niveaux de validation, volontairement séparés :
1. `IntentionAnalysee` (ici) — forme, énumérations, bornes.
2. `valider_colonnes()` (ici aussi) — les colonnes citées existent *et* ont le bon type,
   confronté au profilage réel de 4.2. Aucune seconde source de vérité.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ──────────────────────────────────────────────────────────────────────────────
# Vocabulaire — ce que le moteur sait faire, et rien de plus
# ──────────────────────────────────────────────────────────────────────────────

TypeAnalyse = Literal["classement", "prevision", "tendance", "anomalie"]
Agregation = Literal["somme", "moyenne", "comptage", "minimum", "maximum"]
Ordre = Literal["decroissant", "croissant"]
Sensibilite = Literal["faible", "moyenne", "forte"]
Operateur = Literal["egal", "different", "superieur", "inferieur", "dans"]

# Objectif prédéfini → type d'analyse. Garantit la compatibilité des configurations
# existantes : un objectif choisi sans besoin exprimé reste exécutable sans LLM.
TYPE_PAR_OBJECTIF: dict[str, str] = {
    "comparaison_classement": "classement",
    "prevision_evolution": "prevision",
    "prevision_tendance": "tendance",
    "detection_anomalie": "anomalie",
}

LIBELLE_TYPE: dict[str, str] = {
    "classement": "Comparer et classer",
    "prevision": "Prévoir une évolution",
    "tendance": "Détecter une tendance",
    "anomalie": "Identifier une anomalie",
}


class _Ferme(BaseModel):
    """Base commune : aucune clé hors schéma n'est tolérée."""

    model_config = ConfigDict(extra="forbid")


class Filtre(_Ferme):
    colonne: str
    operateur: Operateur
    valeur: Union[str, float, list[str]]


class SpecClassement(_Ferme):
    """« quels sont mes produits les plus vendus », « lesquels mettre en promotion »."""

    type_analyse: Literal["classement"]
    dimension: str                              # colonne catégorielle (produit, région…)
    mesure: str | None = None                   # None ⇒ comptage de lignes
    agregation: Agregation = "somme"
    ordre: Ordre = "decroissant"
    limite: int = Field(default=10, ge=1, le=100)


class SpecPrevision(_Ferme):
    """« comment vont évoluer mes ventes le mois prochain »."""

    type_analyse: Literal["prevision"]
    colonne_temps: str
    mesure: str
    horizon: int = Field(default=3, ge=1, le=36)
    dimension: str | None = None


class SpecTendance(_Ferme):
    """« mes ventes montent-elles ou baissent-elles »."""

    type_analyse: Literal["tendance"]
    colonne_temps: str
    mesure: str
    dimension: str | None = None


class SpecAnomalie(_Ferme):
    """« y a-t-il des valeurs inhabituelles »."""

    type_analyse: Literal["anomalie"]
    mesure: str
    colonne_temps: str | None = None
    dimension: str | None = None
    sensibilite: Sensibilite = "moyenne"


Specification = Annotated[
    Union[SpecClassement, SpecPrevision, SpecTendance, SpecAnomalie],
    Field(discriminator="type_analyse"),
]


class IntentionAnalysee(_Ferme):
    """Sortie validée de la couche de traduction, entrée de 4.3."""

    specification: Specification
    filtres: list[Filtre] = Field(default_factory=list)
    # Obligatoire : c'est le garde-fou montré à l'entreprise. Sans elle, on rejette.
    reformulation: str = Field(min_length=10, max_length=300)


class SpecificationInvalide(Exception):
    """Réponse du LLM non conforme. Porte un message lisible par l'entreprise."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []

    def message_complet(self) -> str:
        if not self.details:
            return self.message
        return f"{self.message} ({' ; '.join(self.details)})"


# ──────────────────────────────────────────────────────────────────────────────
# Schéma JSON transmis au LLM
# ──────────────────────────────────────────────────────────────────────────────

def schema_json() -> dict:
    """Schéma JSON de `IntentionAnalysee`, rendu tel quel.

    Chaque API impose ses propres restrictions à un schéma de sortie structurée — l'une
    refuse les bornes de longueur, l'autre refuse `$defs`/`$ref`. **Cette adaptation
    appartient à `llm_client`**, pas ici : le schéma est notre contrat, sa mise en forme
    est une affaire de transport. Pydantic revalide à la réception dans tous les cas,
    donc aucune adaptation ne peut affaiblir la garantie.
    """
    return IntentionAnalysee.model_json_schema()


# ──────────────────────────────────────────────────────────────────────────────
# Validation des colonnes contre le profil réel
# ──────────────────────────────────────────────────────────────────────────────

def _est_mesure(col: dict) -> bool:
    """Une colonne analysable comme grandeur : numérique et pas un identifiant."""
    return bool(col.get("est_mesure", col.get("est_numerique", False)))


def _index_colonnes(profil: dict) -> dict[str, dict]:
    """Aplatit le profil de 4.2 en {nom_colonne: {est_date, est_numerique}}."""
    index: dict[str, dict] = {}
    for infos in profil.values():
        for col in infos.get("colonnes", []):
            index[col["nom"]] = col
    return index


def valider_colonnes(intention: IntentionAnalysee, profil: dict) -> None:
    """Second niveau : chaque colonne citée existe et a le bon type.

    Lève `SpecificationInvalide`. Le LLM ne peut donc référencer ni une colonne
    inventée, ni une colonne du mauvais type (une date là où il faut un nombre).
    """
    index = _index_colonnes(profil)
    if not index:
        raise SpecificationInvalide("Aucune colonne disponible pour valider la spécification.")

    spec = intention.specification
    erreurs: list[str] = []

    def _verifier(nom: str | None, role: str, predicat=None, attendu: str = "") -> None:
        if nom is None:
            return
        col = index.get(nom)
        if col is None:
            erreurs.append(f"{role} « {nom} » n'existe pas dans les données sélectionnées")
        elif predicat and not predicat(col):
            erreurs.append(f"{role} « {nom} » n'est pas {attendu}")

    _verifier(getattr(spec, "colonne_temps", None), "La colonne temporelle",
              lambda c: c["est_date"], "une colonne de date")
    # `est_mesure` et non `est_numerique` : un identifiant est numérique sans être une
    # grandeur. Le repli sur `est_numerique` couvre un profil produit avant cette
    # distinction — il ne doit jamais être le cas nominal.
    _verifier(getattr(spec, "mesure", None), "La mesure",
              lambda c: _est_mesure(c), "une grandeur mesurable (les identifiants sont exclus)")
    _verifier(getattr(spec, "dimension", None), "La dimension",
              lambda c: not c["est_date"], "utilisable comme dimension")

    for f in intention.filtres:
        _verifier(f.colonne, "La colonne de filtre")

    if erreurs:
        colonnes = ", ".join(sorted(index))
        raise SpecificationInvalide(
            "La spécification produite ne correspond pas aux données sélectionnées.",
            erreurs + [f"colonnes disponibles : {colonnes}"],
        )


def intention_depuis_resume(resume: dict) -> IntentionAnalysee:
    """Reconstruit une `IntentionAnalysee` depuis un `ResultatTraduction.resume()` stocké.

    Permet d'exécuter une configuration sans refaire d'appel LLM. La validation Pydantic
    s'applique de nouveau : une spécification corrompue en base est rejetée, jamais exécutée.
    """
    try:
        return IntentionAnalysee.model_validate({
            "specification": resume["specification"],
            "filtres": resume.get("filtres", []),
            "reformulation": resume["reformulation"],
        })
    except (KeyError, TypeError, ValidationError) as exc:
        raise SpecificationInvalide(
            "La spécification enregistrée est illisible ou incomplète : relancez la "
            "finalisation de cette configuration pour la reconstruire.",
            [str(exc)[:200]],
        )


def valider_reponse(brut: dict, profil: dict) -> IntentionAnalysee:
    """Valide une réponse LLM : forme (Pydantic) puis colonnes (profil réel)."""
    try:
        intention = IntentionAnalysee.model_validate(brut)
    except ValidationError as exc:
        details = [
            f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}"
            for e in exc.errors()[:5]
        ]
        raise SpecificationInvalide(
            "La réponse du modèle ne respecte pas le format attendu.", details
        )
    valider_colonnes(intention, profil)
    return intention


# ──────────────────────────────────────────────────────────────────────────────
# Repli déterministe — spécification par défaut d'un objectif prédéfini
# ──────────────────────────────────────────────────────────────────────────────

def _premiere(index: dict[str, dict], predicat) -> str | None:
    for nom, col in index.items():
        if predicat(col):
            return nom
    return None


def specification_par_defaut(objectif: str, profil: dict) -> IntentionAnalysee:
    """Spécification déterministe d'un objectif prédéfini, sans aucun appel LLM.

    C'est le chemin garanti sans clé API : le système reste entièrement démontrable.
    """
    type_analyse = TYPE_PAR_OBJECTIF.get(objectif)
    if not type_analyse:
        raise SpecificationInvalide(
            "Aucun objectif prédéfini n'est associé à cette configuration : "
            "décrivez votre besoin pour que l'analyse puisse être préparée."
        )

    index = _index_colonnes(profil)
    date = _premiere(index, lambda c: c["est_date"])
    # Le repli ne choisit jamais un identifiant comme mesure : « prévoir idAdmin » n'a
    # pas plus de sens sans clé API qu'avec.
    mesure = _premiere(index, _est_mesure)
    # Une colonne texte d'abord ; à défaut un indicateur oui/non, qui décrit bien une
    # catégorie même s'il est stocké en 0/1.
    dimension = (
        _premiere(index, lambda c: not c["est_date"] and not c["est_numerique"])
        or _premiere(index, lambda c: c.get("est_booleen", False))
    )

    libelle = LIBELLE_TYPE[type_analyse]
    if type_analyse in ("prevision", "tendance"):
        if not date or not mesure:
            raise SpecificationInvalide(
                f"L'objectif « {libelle} » exige une colonne de date et une mesure numérique : "
                "les données sélectionnées n'en contiennent pas."
            )
        commun = {"colonne_temps": date, "mesure": mesure}
        spec = (
            SpecPrevision(type_analyse="prevision", **commun)
            if type_analyse == "prevision"
            else SpecTendance(type_analyse="tendance", **commun)
        )
        reformulation = (
            f"{libelle} : suivre « {mesure} » dans le temps selon « {date} »."
        )
    elif type_analyse == "anomalie":
        if not mesure:
            raise SpecificationInvalide(
                f"L'objectif « {libelle} » exige au moins une mesure numérique."
            )
        spec = SpecAnomalie(type_analyse="anomalie", mesure=mesure, colonne_temps=date)
        reformulation = f"{libelle} : repérer les valeurs inhabituelles de « {mesure} »."
    else:  # classement
        if not dimension:
            # Sans colonne catégorielle, on classe sur ce qui reste de non numérique
            dimension = _premiere(index, lambda c: not c["est_numerique"])
        if not dimension:
            raise SpecificationInvalide(
                f"L'objectif « {libelle} » exige une colonne à classer "
                "(produit, client, région…) en plus des mesures."
            )
        spec = SpecClassement(type_analyse="classement", dimension=dimension, mesure=mesure)
        detail = f"par « {mesure} »" if mesure else "par nombre de lignes"
        reformulation = f"{libelle} : classer « {dimension} » {detail}, du plus élevé au plus faible."

    return IntentionAnalysee(specification=spec, reformulation=reformulation)
