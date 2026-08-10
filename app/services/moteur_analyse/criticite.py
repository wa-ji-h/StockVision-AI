"""Module 4 — tâche 4.6 : niveau de criticité d'un résultat.

**Définition unique des seuils**, sur le modèle de `fiabilite.py` : ce fichier est le seul
endroit où l'on décide ce qui mérite une alerte. Le Module 5 (alertes) et le Module 6
(visualisation) consomment le résultat sans le recalculer.

Trois principes, validés avant écriture :

1. **Le niveau reflète l'ampleur de ce qui est constaté**, pas la qualité du calcul.
2. **Un résultat non concluant est `normal`, sans exception.** Pas un plafond : le niveau
   *est* `normal`. L'incertitude n'est pas un signal.
3. **La fiabilité plafonne, elle ne pousse jamais.** Un historique mince empêche de monter
   haut ; il ne fait jamais monter d'un cran.

Calcul entièrement déterministe : aucun appel LLM n'intervient ici, et ne doit jamais y
intervenir — une alerte doit pouvoir être rejouée et donner le même verdict.

Module sans dépendance interne : importable par `execution.py` sans cycle possible.
"""

from __future__ import annotations

# (code, rang, libellé). Le rang est numérique pour que le Module 5 filtre par comparaison
# (`rang >= 2`) et que le Module 6 code une couleur, sans table de correspondance.
NIVEAUX_CRITICITE: tuple[tuple[str, int, str], ...] = (
    ("normal", 0, "Normal"),
    ("attention", 1, "À surveiller"),
    ("eleve", 2, "Élevé"),
    ("critique", 3, "Critique"),
)

RANG_PAR_NIVEAU = {code: rang for code, rang, _ in NIVEAUX_CRITICITE}
NIVEAU_PAR_RANG = {rang: code for code, rang, _ in NIVEAUX_CRITICITE}
LIBELLE_PAR_NIVEAU = {code: libelle for code, _, libelle in NIVEAUX_CRITICITE}

RANG_MAX = max(RANG_PAR_NIVEAU.values())

# ──────────────────────────────────────────────────────────────────────────────
# Seuils — tout se règle ici
# ──────────────────────────────────────────────────────────────────────────────
#
# Volontairement génériques : ils ne connaissent pas le métier. Une baisse de 10 % de
# chiffre d'affaires et une baisse de 10 % d'un stock tampon n'ont pas la même gravité.
# Les rendre réglables par configuration sera un **ajout** (lire les seuils depuis la
# configuration au lieu d'ici), jamais une refonte : la forme du calcul ne changera pas.

# Ampleur d'une évolution, en % — franchis dans l'ordre : attention, élevé, critique.
SEUILS_VARIATION: tuple[float, float, float] = (10.0, 25.0, 50.0)

# Part d'observations aberrantes dans le total (0,02 = 2 %).
SEUILS_TAUX_ANOMALIE: tuple[float, float, float] = (0.02, 0.05, 0.10)

# Écart maximal observé, rapporté au seuil de détection retenu. 1,0 = tout juste détecté ;
# 1,6 = une observation à 1,6 fois le seuil, donc franchement hors norme.
SEUILS_ECART_ANOMALIE: tuple[float, float, float] = (1.0, 1.3, 1.6)

# Part du total captée par le premier du classement. Deux seuils seulement : un classement
# ne va pas jusqu'à « critique » (voir PLAFOND_PAR_TYPE).
SEUILS_CONCENTRATION: tuple[float, float] = (0.50, 0.70)

# Au-delà, l'intervalle de prévision est plus large que la valeur elle-même : l'ampleur
# affichée n'est pas exploitable, même si la prévision est formellement concluante.
LARGEUR_RELATIVE_MAX = 1.0

# Un historique mince ne peut pas produire une alerte prioritaire.
PLAFOND_PAR_FIABILITE: dict[str, int] = {"indicative": 1, "limitee": 2, "bonne": RANG_MAX}

# Un classement décrit un état, pas un écart à une attente : jamais « critique ».
PLAFOND_PAR_TYPE: dict[str, int] = {"classement": RANG_PAR_NIVEAU["eleve"]}


def _rang_par_seuils(valeur: float, seuils: tuple[float, ...]) -> tuple[int, float | None]:
    """Rang atteint et dernier seuil franchi. Seuils croissants, un rang par seuil."""
    rang, franchi = 0, None
    for niveau, seuil in enumerate(seuils, start=1):
        if valeur >= seuil:
            rang, franchi = niveau, seuil
    return rang, franchi


# ──────────────────────────────────────────────────────────────────────────────
# Ampleur, par opération
# ──────────────────────────────────────────────────────────────────────────────

def _ampleur_variation(ind: dict, libelle_sens: str) -> dict:
    """Prévision et tendance partagent la même mesure d'ampleur : la variation en %."""
    variation = ind.get("variation_pct")
    if variation is None:
        return {"rang": 0, "indicateur": "variation_pct", "valeur": None, "seuil": None,
                "phrase": "La variation n'a pas pu être mesurée."}
    rang, seuil = _rang_par_seuils(abs(float(variation)), SEUILS_VARIATION)
    sens = ind.get("sens") or ""
    mot = {"hausse": "Hausse", "baisse": "Baisse"}.get(sens, "Évolution")
    return {
        "rang": rang, "indicateur": "variation_pct", "valeur": round(float(variation), 2),
        "seuil": seuil,
        "phrase": f"{mot} {libelle_sens} de {abs(float(variation)):.1f} %.",
    }


def _ampleur_anomalie(ind: dict) -> dict:
    """Deux axes, on retient le plus élevé.

    Dix anomalies légères et une seule très forte méritent toutes deux d'être remontées :
    un seul des deux axes suffirait à en manquer une moitié.
    """
    nb = int(ind.get("nb_anomalies") or 0)
    total = int(ind.get("nb_observations") or 0)
    taux = (nb / total) if total else 0.0
    rang_taux, seuil_taux = _rang_par_seuils(taux, SEUILS_TAUX_ANOMALIE)

    seuil_detection = float(ind.get("seuil_ecarts_types") or 0) or 1.0
    ecart_max = ind.get("ecart_max")
    ratio = (abs(float(ecart_max)) / seuil_detection) if ecart_max is not None else 0.0
    rang_ecart, seuil_ratio = _rang_par_seuils(ratio, SEUILS_ECART_ANOMALIE)

    if rang_ecart > rang_taux:
        return {
            "rang": rang_ecart, "indicateur": "ecart_max",
            "valeur": round(float(ecart_max), 2) if ecart_max is not None else None,
            "seuil": seuil_ratio,
            "phrase": (f"{nb} observation(s) inhabituelle(s) sur {total}, "
                       f"écart maximal de {abs(float(ecart_max)):.1f} écarts-types."),
        }
    return {
        "rang": rang_taux, "indicateur": "taux_anomalies", "valeur": round(taux, 4),
        "seuil": seuil_taux,
        "phrase": f"{nb} observation(s) inhabituelle(s) sur {total} ({taux * 100:.1f} %).",
    }


def _ampleur_classement(ind: dict) -> dict:
    concentration = ind.get("concentration")
    if concentration is None:
        return {"rang": 0, "indicateur": "concentration", "valeur": None, "seuil": None,
                "phrase": "La concentration du classement n'a pas pu être mesurée."}
    rang, seuil = _rang_par_seuils(float(concentration), SEUILS_CONCENTRATION)
    premier = ind.get("premier")
    qui = f"« {premier} »" if premier else "Le premier élément"
    return {
        "rang": rang, "indicateur": "concentration", "valeur": round(float(concentration), 4),
        "seuil": seuil,
        "phrase": f"{qui} concentre {float(concentration) * 100:.0f} % du total.",
    }


_AMPLEURS = {
    "prevision": lambda ind: _ampleur_variation(ind, "prévue"),
    "tendance": lambda ind: _ampleur_variation(ind, "constatée sur la période"),
    "anomalie": _ampleur_anomalie,
    "classement": _ampleur_classement,
}


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────────────

def evaluer_criticite(type_analyse: str, indicateurs: dict, fiabilite: dict | None = None) -> dict:
    """Niveau de criticité d'un résultat, avec la justification de ce niveau.

    La justification est aussi importante que le niveau : une alerte doit pouvoir dire
    pourquoi elle s'est déclenchée, sans que personne ait à rejouer le calcul.
    """
    indicateurs = indicateurs or {}

    # 1. L'incertitude n'est pas un signal. Testé avant tout le reste.
    if not indicateurs.get("conclusif"):
        return _resultat(
            rang=0,
            motif="Le résultat n'est pas concluant : aucun signal exploitable.",
            indicateur="conclusif", valeur=False, seuil=None, plafonnements=[],
        )

    mesure = _AMPLEURS.get(type_analyse)
    if mesure is None:      # inatteignable : le vocabulaire est fermé
        return _resultat(0, f"Opération « {type_analyse} » sans règle de criticité.",
                         None, None, None, [])
    ampleur = mesure(indicateurs)
    rang = ampleur["rang"]
    plafonnements: list[str] = []

    # 2. Prévision trop incertaine : l'ampleur existe mais n'est pas exploitable.
    largeur = indicateurs.get("largeur_relative")
    if (type_analyse == "prevision" and largeur is not None
            and float(largeur) > LARGEUR_RELATIVE_MAX):
        rang, plafond = _plafonner(rang, RANG_PAR_NIVEAU["attention"])
        if plafond:
            plafonnements.append(
                f"intervalle plus large que la valeur elle-même "
                f"(×{float(largeur):.2f}) : plafonné à « {LIBELLE_PAR_NIVEAU['attention']} »"
            )

    # 3. Un classement décrit un état, pas un écart à une attente.
    plafond_type = PLAFOND_PAR_TYPE.get(type_analyse)
    if plafond_type is not None:
        rang, plafond = _plafonner(rang, plafond_type)
        if plafond:
            plafonnements.append(
                f"un classement décrit un état : plafonné à "
                f"« {LIBELLE_PAR_NIVEAU[NIVEAU_PAR_RANG[plafond_type]]} »"
            )

    # 4. La fiabilité plafonne — elle ne fait jamais monter.
    code_fiabilite = (fiabilite or {}).get("code")
    plafond_fiab = PLAFOND_PAR_FIABILITE.get(code_fiabilite)
    if plafond_fiab is not None:
        rang, plafond = _plafonner(rang, plafond_fiab)
        if plafond:
            plafonnements.append(
                f"fiabilité {code_fiabilite} : plafonné à "
                f"« {LIBELLE_PAR_NIVEAU[NIVEAU_PAR_RANG[plafond_fiab]]} »"
            )

    motif = ampleur["phrase"]
    if rang == 0:
        motif += f" En deçà du seuil de vigilance ({SEUILS_VARIATION[0]:.0f} %)." \
            if ampleur["indicateur"] == "variation_pct" else " En deçà du seuil de vigilance."

    return _resultat(rang, motif, ampleur["indicateur"], ampleur["valeur"],
                     ampleur["seuil"], plafonnements)


def _plafonner(rang: int, plafond: int) -> tuple[int, bool]:
    """Rabat un rang sur son plafond, et dit si le plafond a effectivement joué."""
    return (plafond, True) if rang > plafond else (rang, False)


def _resultat(rang: int, motif: str, indicateur, valeur, seuil, plafonnements: list[str]) -> dict:
    niveau = NIVEAU_PAR_RANG[rang]
    return {
        "niveau": niveau,
        "rang": rang,
        "libelle": LIBELLE_PAR_NIVEAU[niveau],
        "motif": motif,
        "indicateur": indicateur,
        "valeur": valeur,
        "seuil_franchi": seuil,
        "plafonnements": plafonnements,
    }
