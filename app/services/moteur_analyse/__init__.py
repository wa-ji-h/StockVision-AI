"""Module 4 — Moteur d'Analyse et de Prévision IA.

Chaîne en place : 4.1 extraction (`extraction.py`) → 4.2 préparation (`preparation.py`).
4.3 (modèles statistiques) puis l'interprétation LLM viendront ici et consommeront
directement le `ResultatPreparation` produit par 4.2, sans réécriture.
"""

from app.services.moteur_analyse.extraction import (
    STATUT_ERREUR_DONNEES,
    STATUT_EXTRACTION_EN_COURS,
    ExtractionError,
    ResultatExtraction,
    colonnes_temporelles,
    executer_extraction,
    extraire_donnees,
    profiler_selection,
)
from app.services.moteur_analyse.fiabilite import (
    NIVEAUX_FIABILITE,
    PAS_PAR_FREQUENCE,
    SEUIL_FIABILITE_BONNE,
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

__all__ = [
    # 4.1 — extraction
    "ExtractionError",
    "ResultatExtraction",
    "extraire_donnees",
    "executer_extraction",
    "profiler_selection",
    "colonnes_temporelles",
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
]
