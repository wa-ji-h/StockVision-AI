"""Niveaux de fiabilité et pas de temps — **définition unique**.

Partagé par la préparation (4.2), le profilage du wizard et les routes du dashboard, pour que
le guidage annonce exactement ce que le moteur appliquera. Module volontairement sans
dépendance interne (hors pandas) : il est importé par `extraction.py` comme par
`preparation.py`, aucun cycle possible.

**Le volume ne bloque jamais une analyse.** Il qualifie sa fiabilité, rien de plus. Seule
l'impossibilité mathématique (pas de date, pas de mesure, aucune donnée) interrompt un calcul.
"""

from __future__ import annotations

import pandas as pd

# (seuil minimal de points, code, libellé, nuance). Ordre décroissant : le premier seuil
# atteint gagne. Modifier ces valeurs suffit à faire évoluer wizard et moteur ensemble.
NIVEAUX_FIABILITE: tuple[tuple[int, str, str, str], ...] = (
    (20, "bonne", "Fiabilité bonne", "l'historique disponible est suffisant"),
    (10, "limitee", "Fiabilité limitée", "un historique plus étendu améliorerait la précision"),
    (0, "indicative", "Fiabilité indicative", "l'historique disponible est limité"),
)

# Seuil au-delà duquel la fiabilité est considérée bonne (utilisé pour les recommandations).
SEUIL_FIABILITE_BONNE = NIVEAUX_FIABILITE[0][0]

# Fréquence de la configuration → règle de rééchantillonnage, code de période et libellé.
# `resample` et `periode` diffèrent volontairement pour le mois : 'MS' cale le rééchantillonnage
# en début de mois, 'M' est le code de période attendu par `to_period`.
PAS_PAR_FREQUENCE: dict[str, dict[str, str]] = {
    "quotidienne": {"resample": "D", "periode": "D", "label": "jour"},
    "hebdomadaire": {"resample": "W", "periode": "W", "label": "semaine"},
    "mensuelle": {"resample": "MS", "periode": "M", "label": "mois"},
}


def evaluer_fiabilite(n_points: int) -> dict:
    """Niveau de fiabilité correspondant à un nombre de points."""
    for seuil, code, label, nuance in NIVEAUX_FIABILITE:
        if n_points >= seuil:
            return {
                "code": code,
                "label": label,
                "nuance": nuance,
                "seuil": seuil,
                "points": int(n_points),
            }
    # NIVEAUX_FIABILITE se termine par un seuil de 0 : cette ligne est un garde-fou.
    return {"code": "indicative", "label": "Fiabilité indicative", "nuance": "", "seuil": 0, "points": int(n_points)}


def phrase_fiabilite(n_points: int) -> str:
    """Phrase destinée à l'entreprise. Constate, n'interrompt pas."""
    f = evaluer_fiabilite(n_points)
    pluriel = "s" if n_points > 1 else ""
    return (
        f"Analyse réalisée sur {n_points} point{pluriel} — "
        f"{f['label'].lower()}, {f['nuance']}."
    )


def points_par_frequence(dates: pd.Series) -> dict[str, int]:
    """Nombre de points obtenus à chaque pas de temps, pour une colonne de dates.

    Sert au guidage du choix de fréquence : la même série donne beaucoup de points au jour
    et très peu au mois. Compte les périodes réellement porteuses de données, pas la
    longueur de la grille.
    """
    propres = pd.to_datetime(dates, errors="coerce").dropna()
    if propres.empty:
        return {freq: 0 for freq in PAS_PAR_FREQUENCE}
    return {
        freq: int(propres.dt.to_period(pas["periode"]).nunique())
        for freq, pas in PAS_PAR_FREQUENCE.items()
    }
