"""Module 4 — Moteur d'Analyse et de Prévision IA.

Chaîne complète, chaque étape consommant la précédente sans réécriture :
4.1 extraction (`extraction.py`) → 4.2 préparation (`preparation.py`) →
4.3 traduction de l'intention (`traduction.py`) → 4.4 exécution (`execution.py`) →
4.5 restitution en langage naturel (`interpretation.py`).

Le partage des rôles ne bouge à aucune étape : le LLM traduit puis commente, le moteur
statistique calcule. `fiabilite.py` porte la définition unique des niveaux, importée
partout et ne dépendant de rien (aucun cycle).
"""

from app.services.moteur_analyse.extraction import (
    STATUT_ERREUR_DONNEES,
    STATUT_EXTRACTION_EN_COURS,
    MINIMUM_VALEURS_BOOLEEN,
    MINIMUM_VALEURS_CLE,
    ExtractionError,
    ResultatExtraction,
    booleens_declares_bdd,
    booleens_declares_sql,
    colonne_est_booleenne,
    colonnes_booleennes,
    colonnes_identifiantes,
    colonnes_temporelles,
    decrire_colonnes,
    nom_evoque_identifiant,
    valeurs_forment_une_cle,
    executer_extraction,
    extraire_donnees,
    profiler_selection,
)
from app.services.moteur_analyse.fiabilite import (
    MESSAGES_ADEQUATION,
    MINIMUM_PERIODES,
    NIVEAUX_FIABILITE,
    PAS_PAR_FREQUENCE,
    SEUIL_FIABILITE_BONNE,
    adequation_frequence,
    evaluer_fiabilite,
    phrase_fiabilite,
    points_par_frequence,
)
from app.services.moteur_analyse.preparation import (
    OBJECTIFS_TEMPORELS,
    PreparationError,
    ResultatPreparation,
    TablePreparee,
    executer_preparation,
    preparer_donnees,
)

from app.services.moteur_analyse.schema_analyse import (
    TYPE_PAR_OBJECTIF,
    IntentionAnalysee,
    SpecificationInvalide,
    intention_depuis_resume,
    schema_json,
    specification_par_defaut,
    valider_colonnes,
    valider_reponse,
)
from app.services.moteur_analyse.criticite import (
    LIBELLE_PAR_NIVEAU,
    NIVEAUX_CRITICITE,
    NIVEAU_PAR_RANG,
    PLAFOND_PAR_FIABILITE,
    PLAFOND_PAR_TYPE,
    RANG_PAR_NIVEAU,
    SEUILS_CONCENTRATION,
    SEUILS_ECART_ANOMALIE,
    SEUILS_TAUX_ANOMALIE,
    SEUILS_VARIATION,
    evaluer_criticite,
)
from app.services.moteur_analyse.execution import (
    LIBELLE_MODELE,
    MODELE_PAR_FIABILITE,
    SEUIL_PAR_SENSIBILITE,
    ExecutionError,
    ResultatExecution,
    executer_calcul,
    executer_et_stocker,
)
from app.services.moteur_analyse.llm_client import (
    FOURNISSEURS,
    LLMIndisponible,
    appeler_llm,
    llm_disponible,
    nom_fournisseur,
)
from app.services.moteur_analyse.traduction import (
    ResultatTraduction,
    traduire_intention,
)
from app.services.moteur_analyse.interpretation import (
    CIBLES_LONGUEUR,
    LIBELLE_CONFIANCE,
    LIMITES_MAX,
    FormatInvalide,
    InterpretationInvalide,
    InterpretationRedigee,
    ResultatInterpretation,
    charge_utile,
    confiance_maximale,
    interpreter_et_stocker,
    interpreter_resultat,
)

__all__ = [
    # 4.1 — extraction
    "ExtractionError",
    "ResultatExtraction",
    "extraire_donnees",
    "executer_extraction",
    "profiler_selection",
    "colonnes_temporelles",
    # Description typée d'un DataFrame — **seule** manière de produire un profil, pour que
    # le wizard et le diagnostic de lancement ne puissent pas diverger.
    "decrire_colonnes",
    # Numériques au sens du dtype, jamais des grandeurs : identifiants et indicateurs oui/non
    "colonnes_identifiantes",
    "nom_evoque_identifiant",
    "valeurs_forment_une_cle",
    "colonnes_booleennes",
    "colonne_est_booleenne",
    "booleens_declares_bdd",
    "booleens_declares_sql",
    "MINIMUM_VALEURS_CLE",
    "MINIMUM_VALEURS_BOOLEEN",
    "STATUT_EXTRACTION_EN_COURS",
    "STATUT_ERREUR_DONNEES",
    # 4.2 — préparation
    "PreparationError",
    "ResultatPreparation",
    "TablePreparee",
    "preparer_donnees",
    "executer_preparation",
    "OBJECTIFS_TEMPORELS",
    # Fiabilité — définition unique partagée par le moteur et le wizard
    "NIVEAUX_FIABILITE",
    "SEUIL_FIABILITE_BONNE",
    "PAS_PAR_FREQUENCE",
    "evaluer_fiabilite",
    "phrase_fiabilite",
    "points_par_frequence",
    # Traduction en langage métier de ce que le pas de temps change
    "adequation_frequence",
    "MESSAGES_ADEQUATION",
    "MINIMUM_PERIODES",
    # Traduction de l'intention (LLM) — schéma fermé + repli déterministe
    "IntentionAnalysee",
    "SpecificationInvalide",
    "ResultatTraduction",
    "traduire_intention",
    "specification_par_defaut",
    "valider_reponse",
    "valider_colonnes",
    "schema_json",
    "intention_depuis_resume",
    # Accès au fournisseur — le seul endroit qui sait quelle API est appelée
    "llm_disponible",
    "appeler_llm",
    "LLMIndisponible",
    "FOURNISSEURS",
    "nom_fournisseur",
    "TYPE_PAR_OBJECTIF",
    # Exécution — le moteur de calcul
    "ExecutionError",
    "ResultatExecution",
    "executer_calcul",
    "executer_et_stocker",
    "MODELE_PAR_FIABILITE",
    "LIBELLE_MODELE",
    "SEUIL_PAR_SENSIBILITE",
    # 4.6 — criticité : définition unique des seuils, base des alertes du Module 5
    "evaluer_criticite",
    "NIVEAUX_CRITICITE",
    "RANG_PAR_NIVEAU",
    "NIVEAU_PAR_RANG",
    "LIBELLE_PAR_NIVEAU",
    "SEUILS_VARIATION",
    "SEUILS_TAUX_ANOMALIE",
    "SEUILS_ECART_ANOMALIE",
    "SEUILS_CONCENTRATION",
    "PLAFOND_PAR_FIABILITE",
    "PLAFOND_PAR_TYPE",
    # 4.5 — restitution en langage naturel (le LLM commente, il ne calcule pas)
    "InterpretationInvalide",
    "FormatInvalide",
    "InterpretationRedigee",
    "LIMITES_MAX",
    "CIBLES_LONGUEUR",
    "ResultatInterpretation",
    "interpreter_resultat",
    "interpreter_et_stocker",
    "charge_utile",
    "confiance_maximale",
    "LIBELLE_CONFIANCE",
]
