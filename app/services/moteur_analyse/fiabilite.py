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


# ──────────────────────────────────────────────────────────────────────────────
# Adéquation d'une fréquence — le même calcul, dit en langage métier
# ──────────────────────────────────────────────────────────────────────────────
#
# Les points et le niveau de fiabilité restent calculés et stockés : ils servent au
# diagnostic et à l'interprétation. Ils ne sont simplement plus ce qu'on **montre** —
# « 20 pts · bonne » ne se lit pas sans bagage statistique.
#
# Dérivé de NIVEAUX_FIABILITE, jamais une seconde échelle : une fréquence est « adaptée »
# exactement quand sa fiabilité est bonne. Déplacer le seuil déplace les deux ensemble.

# En dessous de 2 périodes il n'y a pas de série : rien à comparer, rien à projeter.
MINIMUM_PERIODES = 2

MESSAGES_ADEQUATION: dict[str, str | None] = {
    # Fréquence adaptée : aucun message. Pas de bruit visuel quand tout va bien.
    "adaptee": None,
    "peu_adaptee": (
        "Vos données couvrent une période courte — à ce rythme, le résultat sera peu précis."
    ),
    "impossible": (
        "Vos données ne couvrent qu'une seule période à ce rythme. "
        "Choisissez une fréquence plus fine pour que l'analyse soit réalisable."
    ),
}


def adequation_frequence(n_points: int) -> dict:
    """Ce que le pas de temps change pour l'entreprise, sans vocabulaire technique.

    `bloquant` distingue le rythme qui rend l'analyse irréalisable de celui qui la rend
    seulement imprécise — c'est cette distinction, et non le nombre de points, qui doit
    remonter jusqu'à l'interface.
    """
    if n_points < MINIMUM_PERIODES:
        code = "impossible"
    elif n_points < SEUIL_FIABILITE_BONNE:
        code = "peu_adaptee"
    else:
        code = "adaptee"
    return {
        "code": code,
        "message": MESSAGES_ADEQUATION[code],
        "bloquant": code == "impossible",
    }


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
