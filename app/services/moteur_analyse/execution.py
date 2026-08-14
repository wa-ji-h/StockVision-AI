"""Module 4 — exécution : le moteur de calcul.

Consomme une `IntentionAnalysee` validée (schéma fermé) et un `ResultatPreparation`, et
produit les chiffres. **Ne lit jamais de texte libre** : tout ce qui arrive ici a traversé
Pydantic et la vérification des colonnes contre le profil réel.

Répartition des rôles rappelée :
- LLM : traduit l'intention, puis interprétera le résultat. Ne calcule jamais.
- Ce module : calcule. Ne lit jamais de texte libre.

Le modèle de prévision est choisi d'après la **fiabilité calculée en 4.2**, de sorte qu'une
analyse reste possible quel que soit le volume, sans jamais produire un résultat plus assuré
que les données ne le permettent. Le modèle réellement appliqué est renvoyé avec le résultat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.services.moteur_analyse.preparation import TablePreparee
from app.services.moteur_analyse.criticite import evaluer_criticite
from app.services.moteur_analyse.schema_analyse import IntentionAnalysee

_log = logging.getLogger("stockvision.moteur_analyse")

# Fiabilité (calculée en 4.2) → modèle de prévision appliqué.
MODELE_PAR_FIABILITE: dict[str, str] = {
    "bonne": "arima",
    "limitee": "lissage_exponentiel",
    "indicative": "regression_lineaire",
}

LIBELLE_MODELE: dict[str, str] = {
    "arima": "ARIMA",
    "lissage_exponentiel": "Lissage exponentiel",
    "regression_lineaire": "Régression linéaire",
    "moindres_carres": "Régression linéaire",
    "ecart_type": "Écart-type (z-score)",
    "agregation": "Agrégation",
}

# Sensibilité de détection → seuil de z-score. Plus la sensibilité est forte, plus le
# seuil est bas, donc plus d'observations sont signalées.
SEUIL_PAR_SENSIBILITE: dict[str, float] = {
    "faible": 3.0,
    "moyenne": 2.5,
    "forte": 2.0,
}

# En dessous, un modèle de série temporelle n'a rien à apprendre.
MIN_POINTS_MODELE = 3


class ExecutionError(Exception):
    """Erreur métier d'exécution, porteuse d'un message lisible par l'entreprise."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []

    def message_complet(self) -> str:
        if not self.details:
            return self.message
        return f"{self.message} ({' ; '.join(self.details)})"


@dataclass
class ResultatExecution:
    """Sortie du moteur. Consommable telle quelle par l'interprétation et le stockage."""

    config_id: int
    type_analyse: str
    valeur_analyse: float | str | None          # le chiffre principal
    valeur_prevue: float | None = None          # projection, pour les objectifs temporels
    intervalle_bas: float | None = None
    intervalle_haut: float | None = None
    methode_intervalle: str | None = None       # "modele" | "residus" | None
    modele_applique: str = "agregation"
    fiabilite: dict = field(default_factory=dict)
    unite: str | None = None                    # colonne mesurée
    # Indicateurs **structurés** propres à chaque opération. Jamais du texte libre :
    # l'interprétation et l'affichage les lisent directement, sans rien parser.
    indicateurs: dict = field(default_factory=dict)
    # Données de visualisation, prêtes pour un futur graphique
    serie_historique: list[dict] = field(default_factory=list)
    serie_prevue: list[dict] = field(default_factory=list)
    elements_classes: list[dict] = field(default_factory=list)
    observations_aberrantes: list[dict] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    # Identifiant de la ligne `ResultatAnalyse` écrite par `executer_et_stocker`.
    # C'est par lui que 4.5 retrouve le résultat à annoter, sans le rechercher.
    id_resultat: int | None = None
    # Tâche 4.6 — niveau de criticité et sa justification. Déduit des indicateurs par
    # `criticite.py`, jamais par un modèle de langage : une alerte doit être rejouable.
    criticite: dict = field(default_factory=dict)

    @property
    def modele_libelle(self) -> str:
        return LIBELLE_MODELE.get(self.modele_applique, self.modele_applique)

    def resume(self) -> dict:
        """Structure sérialisable — c'est elle qui sera stockée et interprétée."""
        return {
            "config_id": self.config_id,
            "type_analyse": self.type_analyse,
            "valeur_analyse": self.valeur_analyse,
            "valeur_prevue": self.valeur_prevue,
            "intervalle": (
                {"bas": self.intervalle_bas, "haut": self.intervalle_haut,
                 "methode": self.methode_intervalle}
                if self.intervalle_bas is not None else None
            ),
            "modele_applique": self.modele_applique,
            "modele_libelle": self.modele_libelle,
            "fiabilite": self.fiabilite,
            "unite": self.unite,
            "indicateurs": self.indicateurs,
            # Remonté à la racine en plus d'`indicateurs` : le Module 5 lit un niveau
            # d'alerte, il n'a pas à fouiller un sous-objet pour le trouver.
            "criticite": self.criticite,
            "visualisation": {
                "serie_historique": self.serie_historique,
                "serie_prevue": self.serie_prevue,
                "elements_classes": self.elements_classes,
                "observations_aberrantes": self.observations_aberrantes,
            },
            "avertissements": self.avertissements,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Filtres — appliqués avant tout calcul
# ──────────────────────────────────────────────────────────────────────────────

def _stats_communes(valeurs) -> dict:
    """Moyenne, écart-type et effectif — **mêmes clés pour les quatre opérations**.

    L'anomalie produisait déjà `moyenne`, `ecart_type` et `nb_observations` : ces noms sont
    repris tels quels plutôt que doublés, pour qu'un consommateur (alertes, visualisation)
    n'ait jamais à brancher selon le type d'analyse.

    `ddof=0` : on décrit la série analysée, on n'estime pas la variance d'une population
    dont elle serait un échantillon — c'est aussi ce que fait déjà la détection d'anomalie.
    """
    serie = pd.Series(list(valeurs), dtype="float64").dropna()
    if serie.empty:
        return {"moyenne": None, "ecart_type": None, "nb_observations": 0}
    return {
        "moyenne": round(float(serie.mean()), 4),
        "ecart_type": round(float(serie.std(ddof=0)), 4) if len(serie) > 1 else 0.0,
        "nb_observations": int(len(serie)),
    }


def _concentration(elements: list[dict]) -> float | None:
    """Part du total captée par le premier du classement, sur des valeurs positives.

    Renvoie `None` dès qu'une valeur est négative : une part de total n'a pas de sens sur
    des grandeurs signées (une marge négative fausserait le dénominateur sans le dire).
    """
    valeurs = [float(e["valeur"]) for e in elements]
    if not valeurs or any(v < 0 for v in valeurs):
        return None
    total = sum(valeurs)
    if total <= 0:
        return None
    return round(max(valeurs) / total, 4)


def _ecart_type_residus(observees, ajustees) -> float | None:
    """Dispersion des résidus (observé − ajusté) : l'écart réel du modèle aux données.

    À ne pas confondre avec `variation_pct`, qui mesure la projection d'un bout à l'autre.
    L'un dit « de combien le modèle se trompe sur ce qu'il a vu », l'autre « de combien la
    valeur évolue ». Deux questions distinctes, deux indicateurs distincts.
    """
    obs = np.asarray(list(observees), dtype=float)
    aju = np.asarray(list(ajustees), dtype=float)
    if obs.size == 0 or obs.size != aju.size:
        return None
    residus = obs - aju
    if not np.isfinite(residus).any():
        return None
    return round(float(np.nanstd(residus)), 4)


def _appliquer_filtres(df: pd.DataFrame, filtres: list) -> tuple[pd.DataFrame, list[str]]:
    """Restreint le jeu de données. Les opérateurs viennent d'une énumération fermée :
    aucune expression arbitraire n'est évaluée."""
    avertissements: list[str] = []
    for f in filtres:
        if f.colonne not in df.columns:
            avertissements.append(f"Filtre ignoré : la colonne « {f.colonne} » est absente des données préparées.")
            continue
        serie = df[f.colonne]
        try:
            if f.operateur == "egal":
                masque = serie == f.valeur
            elif f.operateur == "different":
                masque = serie != f.valeur
            elif f.operateur == "superieur":
                masque = pd.to_numeric(serie, errors="coerce") > float(f.valeur)
            elif f.operateur == "inferieur":
                masque = pd.to_numeric(serie, errors="coerce") < float(f.valeur)
            elif f.operateur == "dans":
                valeurs = f.valeur if isinstance(f.valeur, list) else [f.valeur]
                masque = serie.isin(valeurs)
            else:  # inatteignable : l'énumération est fermée
                continue
        except (TypeError, ValueError):
            avertissements.append(f"Filtre ignoré sur « {f.colonne} » : valeur incompatible avec la colonne.")
            continue
        df = df[masque.fillna(False)]
    return df, avertissements


# ──────────────────────────────────────────────────────────────────────────────
# Opération : classement
# ──────────────────────────────────────────────────────────────────────────────

_AGREGATIONS = {
    "somme": "sum",
    "moyenne": "mean",
    "comptage": "count",
    "minimum": "min",
    "maximum": "max",
}


def _executer_classement(spec, df: pd.DataFrame, fiabilite: dict) -> ResultatExecution:
    if spec.dimension not in df.columns:
        raise ExecutionError(
            f"La colonne « {spec.dimension} » est absente des données préparées : "
            "elle a pu être écartée parce qu'elle était vide."
        )

    groupes = df.groupby(spec.dimension, dropna=True)
    if spec.mesure is None:
        serie = groupes.size()
        unite = "nombre de lignes"
    else:
        if spec.mesure not in df.columns:
            raise ExecutionError(f"La mesure « {spec.mesure} » est absente des données préparées.")
        serie = groupes[spec.mesure].agg(_AGREGATIONS[spec.agregation])
        unite = spec.mesure

    serie = serie.dropna()
    if serie.empty:
        raise ExecutionError(f"Aucune valeur à classer pour « {spec.dimension} ».")

    serie = serie.sort_values(ascending=(spec.ordre == "croissant")).head(spec.limite)
    elements = [
        {"rang": i + 1, "element": str(idx), "valeur": float(val)}
        for i, (idx, val) in enumerate(serie.items())
    ]

    return ResultatExecution(
        config_id=0,
        type_analyse="classement",
        # La valeur principale est celle du premier du classement : c'est ce qui répond
        # à « quel est mon meilleur / mon pire ».
        valeur_analyse=elements[0]["valeur"],
        modele_applique="agregation",
        fiabilite=fiabilite,
        unite=unite,
        indicateurs={
            "sens": "classement",
            "conclusif": len(elements) >= 2,   # classer un seul element ne classe rien
            "nb_elements": len(elements),
            "agregation": spec.agregation if spec.mesure else "comptage",
            "ordre": spec.ordre,
            "premier": elements[0]["element"],
            "valeur_premier": elements[0]["valeur"],
            "dernier": elements[-1]["element"],
            "valeur_dernier": elements[-1]["valeur"],
            "ecart_premier_dernier": round(elements[0]["valeur"] - elements[-1]["valeur"], 4),
            # Part du total captée par le premier : c'est elle qui porte l'ampleur d'un
            # classement. Un écart premier/dernier ne dit rien sans l'échelle du total.
            "concentration": _concentration(elements),
            **_stats_communes(e["valeur"] for e in elements),
        },
        elements_classes=elements,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Séries temporelles — préparation commune
# ──────────────────────────────────────────────────────────────────────────────

def _serie_temporelle(spec, df: pd.DataFrame) -> pd.Series:
    """Série numérique indexée par le temps, nettoyée et triée."""
    if spec.mesure not in df.columns:
        raise ExecutionError(f"La mesure « {spec.mesure} » est absente des données préparées.")

    serie = pd.to_numeric(df[spec.mesure], errors="coerce").dropna()
    if not isinstance(serie.index, pd.DatetimeIndex):
        # 4.2 indexe par le temps pour les objectifs temporels ; si la colonne est restée
        # en colonne (fréquence ponctuelle), on la remet en index.
        if spec.colonne_temps in df.columns:
            index = pd.to_datetime(df.loc[serie.index, spec.colonne_temps], errors="coerce")
            serie = serie[index.notna()]
            serie.index = index.dropna()
        else:
            raise ExecutionError(
                f"La colonne temporelle « {spec.colonne_temps} » est introuvable dans les données préparées."
            )

    serie = serie.sort_index()
    if len(serie) < MIN_POINTS_MODELE:
        raise ExecutionError(
            f"Seulement {len(serie)} point(s) exploitable(s) : il en faut au moins "
            f"{MIN_POINTS_MODELE} pour calculer une évolution."
        )
    return serie


def _pas_median(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Pas de temps médian, pour projeter les dates futures."""
    if len(index) < 2:
        return pd.Timedelta(days=1)
    ecarts = pd.Series(index).diff().dropna()
    median = ecarts.median()
    return median if pd.notna(median) and median > pd.Timedelta(0) else pd.Timedelta(days=1)


def _dates_futures(index: pd.DatetimeIndex, horizon: int) -> list[pd.Timestamp]:
    pas = _pas_median(index)
    dernier = index[-1]
    return [dernier + pas * (i + 1) for i in range(horizon)]


def _serialiser_serie(serie: pd.Series) -> list[dict]:
    return [
        {"date": idx.isoformat() if hasattr(idx, "isoformat") else str(idx), "valeur": float(v)}
        for idx, v in serie.items()
        if pd.notna(v)
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Opération : prévision — le modèle dépend de la fiabilité
# ──────────────────────────────────────────────────────────────────────────────

def _prevoir_arima(serie: pd.Series, horizon: int):
    """ARIMA : le seul des trois qui fournit un intervalle de confiance natif."""
    from statsmodels.tsa.arima.model import ARIMA

    valeurs = serie.to_numpy(dtype=float)
    modele = ARIMA(valeurs, order=(1, 1, 1)).fit()
    prevision = modele.get_forecast(steps=horizon)
    moyenne = np.asarray(prevision.predicted_mean, dtype=float)
    bornes = np.asarray(prevision.conf_int(alpha=0.05), dtype=float)
    # Le premier résidu suit la différenciation (d=1) et n'a pas de sens : on l'écarte,
    # sinon l'écart d'ajustement est dominé par une valeur d'amorçage.
    residus = np.asarray(modele.resid, dtype=float)[1:]
    sigma = round(float(np.nanstd(residus)), 4) if residus.size else None
    return moyenne, bornes[:, 0], bornes[:, 1], "modele", sigma


def _prevoir_lissage(serie: pd.Series, horizon: int):
    """Lissage exponentiel de Holt : pas d'intervalle natif, on l'estime sur les résidus."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    valeurs = serie.to_numpy(dtype=float)
    modele = ExponentialSmoothing(valeurs, trend="add", seasonal=None,
                                  initialization_method="estimated").fit()
    moyenne = np.asarray(modele.forecast(horizon), dtype=float)
    residus = valeurs - np.asarray(modele.fittedvalues, dtype=float)
    sigma = float(np.nanstd(residus))
    marge = 1.96 * sigma
    return moyenne, moyenne - marge, moyenne + marge, "residus", round(sigma, 4)


def _prevoir_regression(serie: pd.Series, horizon: int):
    """Régression linéaire sur le rang temporel — le repli quand les points sont rares."""
    from sklearn.linear_model import LinearRegression

    valeurs = serie.to_numpy(dtype=float)
    x = np.arange(len(valeurs), dtype=float).reshape(-1, 1)
    modele = LinearRegression().fit(x, valeurs)
    x_futur = np.arange(len(valeurs), len(valeurs) + horizon, dtype=float).reshape(-1, 1)
    moyenne = modele.predict(x_futur)
    residus = valeurs - modele.predict(x)
    sigma = float(np.nanstd(residus))
    marge = 1.96 * sigma
    return moyenne, moyenne - marge, moyenne + marge, "residus", round(sigma, 4)


_PREVISION = {
    "arima": _prevoir_arima,
    "lissage_exponentiel": _prevoir_lissage,
    "regression_lineaire": _prevoir_regression,
}


def _executer_prevision(spec, df: pd.DataFrame, fiabilite: dict) -> ResultatExecution:
    serie = _serie_temporelle(spec, df)
    code_fiabilite = fiabilite.get("code", "indicative")
    modele = MODELE_PAR_FIABILITE.get(code_fiabilite, "regression_lineaire")
    avertissements: list[str] = []

    try:
        moyenne, bas, haut, methode, erreur_ajustement = _PREVISION[modele](serie, spec.horizon)
    except Exception as exc:
        # Un modèle peut ne pas converger sur des données réelles : on ne renonce pas à
        # l'analyse, on redescend d'un cran et on le dit.
        _log.warning("[execution] %s a échoué (%s) — repli sur la régression linéaire", modele, exc)
        avertissements.append(
            f"{LIBELLE_MODELE[modele]} n'a pas convergé sur ces données : "
            "une régression linéaire a été appliquée à la place."
        )
        modele = "regression_lineaire"
        moyenne, bas, haut, methode, erreur_ajustement = _prevoir_regression(serie, spec.horizon)

    dates = _dates_futures(serie.index, spec.horizon)
    serie_prevue = [
        {"date": d.isoformat(), "valeur": float(m), "bas": float(b), "haut": float(h)}
        for d, m, b, h in zip(dates, moyenne, bas, haut)
    ]

    depart = float(serie.iloc[-1])
    prevue, borne_bas, borne_haut = float(moyenne[-1]), float(bas[-1]), float(haut[-1])
    indicateurs = _qualifier_prevision(depart, prevue, borne_bas, borne_haut)
    indicateurs.update(_stats_communes(serie.to_numpy(dtype=float)))
    # Écart entre la dernière valeur observée et la valeur projetée, en unités de la
    # mesure : `variation_pct` en donne déjà la version relative.
    indicateurs["ecart_absolu"] = round(prevue - depart, 4)
    indicateurs["erreur_ajustement"] = erreur_ajustement
    if not indicateurs["conclusif"]:
        avertissements.append(
            f"L'intervalle de confiance [{borne_bas:.2f} ; {borne_haut:.2f}] englobe la valeur "
            f"de départ ({depart:.2f}) : le modèle ne permet pas de conclure sur le sens de "
            "l'évolution. Ne présentez pas ce résultat comme une hausse ou une baisse."
        )

    return ResultatExecution(
        config_id=0,
        type_analyse="prevision",
        valeur_analyse=depart,                     # dernière valeur observée
        valeur_prevue=prevue,                      # valeur au bout de l'horizon
        intervalle_bas=borne_bas,
        intervalle_haut=borne_haut,
        methode_intervalle=methode,
        modele_applique=modele,
        fiabilite=fiabilite,
        unite=spec.mesure,
        indicateurs=indicateurs,
        serie_historique=_serialiser_serie(serie),
        serie_prevue=serie_prevue,
        avertissements=avertissements,
    )


def _qualifier_prevision(depart: float, prevue: float, bas: float, haut: float) -> dict:
    """Dit si la prévision permet de conclure — et sinon, le dit explicitement.

    Un intervalle qui englobe la valeur de départ signifie que le modèle ne distingue pas
    une hausse d'une baisse. Sans cet indicateur, l'interprétation présenterait comme une
    tendance ce qui n'est que de l'incertitude. C'est structuré, pas du texte à parser.
    """
    conclusif = not (bas <= depart <= haut)
    if not conclusif:
        sens = "indetermine"
    elif prevue > depart:
        sens = "hausse"
    else:
        sens = "baisse"

    # Largeur de l'intervalle rapportée à l'échelle de la valeur prévue : un intervalle de
    # ±5 n'a pas le même poids sur une valeur de 30 que sur une valeur de 3000.
    echelle = abs(prevue) if prevue else (abs(depart) or 1.0)
    largeur_relative = (haut - bas) / echelle

    return {
        "sens": sens,                                            # hausse | baisse | indetermine
        "conclusif": conclusif,
        "intervalle_contient_depart": not conclusif,
        "valeur_depart": round(depart, 4),
        "largeur_intervalle": round(haut - bas, 4),
        "largeur_relative": round(largeur_relative, 4),          # 1.0 = aussi large que la valeur
        "variation_pct": round((prevue - depart) / abs(depart) * 100.0, 2) if depart else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Opération : tendance
# ──────────────────────────────────────────────────────────────────────────────

def _executer_tendance(spec, df: pd.DataFrame, fiabilite: dict) -> ResultatExecution:
    from sklearn.linear_model import LinearRegression

    serie = _serie_temporelle(spec, df)
    valeurs = serie.to_numpy(dtype=float)
    x = np.arange(len(valeurs), dtype=float).reshape(-1, 1)
    modele = LinearRegression().fit(x, valeurs)

    pente = float(modele.coef_[0])
    r2 = float(modele.score(x, valeurs))
    depart, arrivee = float(modele.predict([[0.0]])[0]), float(modele.predict([[len(valeurs) - 1.0]])[0])
    variation = ((arrivee - depart) / abs(depart) * 100.0) if depart else 0.0

    # Seuil relatif : une pente n'a de sens que rapportée à l'échelle des valeurs.
    echelle = float(np.nanmean(np.abs(valeurs))) or 1.0
    if abs(pente) < echelle * 0.001:
        sens = "stable"
    else:
        sens = "hausse" if pente > 0 else "baisse"

    # Un R² faible signifie que la droite explique mal les données : la tendance existe
    # mathématiquement mais ne décrit pas le comportement réel. Même logique que
    # l'intervalle de la prévision — il faut le dire, pas le laisser deviner.
    fiable = r2 >= 0.5
    avertissements: list[str] = []
    if sens != "stable" and not fiable:
        avertissements.append(
            f"L'ajustement est faible (R² = {r2:.2f}) : les données s'écartent beaucoup de "
            "cette tendance. À présenter comme une orientation générale, pas comme une "
            "évolution régulière."
        )

    return ResultatExecution(
        config_id=0,
        type_analyse="tendance",
        valeur_analyse=round(variation, 2),         # variation en % sur la période
        modele_applique="moindres_carres",
        fiabilite=fiabilite,
        unite=spec.mesure,
        indicateurs={
            "sens": sens,                            # hausse | baisse | stable
            "conclusif": sens != "stable" and fiable,
            "pente": round(pente, 6),
            "r2": round(r2, 4),
            "variation_pct": round(variation, 2),
            "valeur_depart": round(depart, 4),
            "valeur_arrivee": round(arrivee, 4),
            "ecart_absolu": round(arrivee - depart, 4),
            # Dispersion des résidus autour de la droite : de combien le modèle se trompe
            # sur ce qu'il a déjà vu. Distinct de la variation, qui mesure l'évolution.
            "erreur_ajustement": _ecart_type_residus(serie.to_numpy(dtype=float), modele.predict(x)),
            **_stats_communes(serie.to_numpy(dtype=float)),
        },
        serie_historique=_serialiser_serie(serie),
        serie_prevue=[
            {"date": idx.isoformat(), "valeur": float(v)}
            for idx, v in zip(serie.index, modele.predict(x))
        ],
        avertissements=avertissements,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Opération : anomalie
# ──────────────────────────────────────────────────────────────────────────────

def _executer_anomalie(spec, df: pd.DataFrame, fiabilite: dict) -> ResultatExecution:
    if spec.mesure not in df.columns:
        raise ExecutionError(f"La mesure « {spec.mesure} » est absente des données préparées.")

    serie = pd.to_numeric(df[spec.mesure], errors="coerce").dropna()
    if len(serie) < MIN_POINTS_MODELE:
        raise ExecutionError(
            f"Seulement {len(serie)} valeur(s) exploitable(s) : il en faut au moins "
            f"{MIN_POINTS_MODELE} pour juger qu'une observation est inhabituelle."
        )

    moyenne = float(serie.mean())
    ecart = float(serie.std(ddof=0))
    seuil = SEUIL_PAR_SENSIBILITE.get(spec.sensibilite, 2.5)

    if ecart == 0:
        return ResultatExecution(
            config_id=0, type_analyse="anomalie", valeur_analyse=0,
            modele_applique="ecart_type", fiabilite=fiabilite, unite=spec.mesure,
            # Indicateurs renseignés même ici : un consommateur ne doit jamais tomber sur
            # un bloc vide selon le chemin pris par le calcul.
            indicateurs={
                "sens": "anomalie", "conclusif": False, "nb_anomalies": 0,
                "seuil_ecarts_types": seuil, "sensibilite": spec.sensibilite,
                "ecart_max": None, **_stats_communes(serie.to_numpy(dtype=float)),
            },
            serie_historique=_serialiser_serie(serie),
            avertissements=["Toutes les valeurs sont identiques : aucune anomalie possible."],
        )

    z = (serie - moyenne) / ecart
    aberrantes = serie[z.abs() > seuil]
    observations = [
        {
            "date": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
            "valeur": float(v),
            # `ecart_zscore` et non `ecart_type` : c'est l'écart de CETTE observation,
            # exprimé en écarts-types, pas l'écart-type de la série. L'ancien nom prêtait
            # à confusion avec l'indicateur global du même nom, et `charge_utile` lisait
            # déjà `ecart_zscore` — l'ampleur des anomalies ne parvenait donc jamais à
            # l'interprétation.
            "ecart_zscore": round(float(z.loc[idx]), 2),
            "sens": "au-dessus" if z.loc[idx] > 0 else "en-dessous",
        }
        for idx, v in aberrantes.items()
    ]

    return ResultatExecution(
        config_id=0,
        type_analyse="anomalie",
        valeur_analyse=len(observations),           # nombre d'anomalies détectées
        modele_applique="ecart_type",
        fiabilite=fiabilite,
        unite=spec.mesure,
        indicateurs={
            "sens": "anomalie",
            "conclusif": len(observations) > 0,
            "nb_anomalies": len(observations),
            "seuil_ecarts_types": seuil,
            "sensibilite": spec.sensibilite,
            "moyenne": round(moyenne, 4),
            "ecart_type": round(ecart, 4),
            "nb_observations": len(serie),
            # Écart le plus fort parmi les anomalies retenues : c'est lui qui distingue
            # « beaucoup de petits écarts » de « une valeur franchement hors norme ».
            "ecart_max": max((abs(o["ecart_zscore"]) for o in observations), default=None),
        },
        serie_historique=_serialiser_serie(serie),
        observations_aberrantes=observations,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────────────

_OPERATIONS = {
    "classement": _executer_classement,
    "prevision": _executer_prevision,
    "tendance": _executer_tendance,
    "anomalie": _executer_anomalie,
}


def executer_calcul(intention: IntentionAnalysee, table: TablePreparee, config_id: int = 0) -> ResultatExecution:
    """Exécute la spécification sur les données préparées.

    Lève `ExecutionError` (message en français) pour tout échec métier.
    """
    spec = intention.specification
    df = table.donnees.copy()          # copie : les données préparées ne sont jamais modifiées

    df, avert_filtres = _appliquer_filtres(df, intention.filtres)
    if df.empty:
        raise ExecutionError("Aucune donnée ne subsiste après application des filtres.")

    operation = _OPERATIONS.get(spec.type_analyse)
    if operation is None:  # inatteignable : le schéma est fermé
        raise ExecutionError(f"Opération non prise en charge : « {spec.type_analyse} ».")

    resultat = operation(spec, df, table.fiabilite)
    resultat.config_id = config_id
    resultat.avertissements = avert_filtres + resultat.avertissements

    # Tâche 4.6 — la criticité se déduit ici, **une seule fois**, quel que soit le chemin
    # pris par l'opération. La calculer dans chaque branche la ferait diverger.
    resultat.criticite = evaluer_criticite(
        resultat.type_analyse, resultat.indicateurs, resultat.fiabilite
    )
    resultat.indicateurs["criticite"] = resultat.criticite
    return resultat


def executer_et_stocker(db, config, intention: IntentionAnalysee, prepare) -> ResultatExecution | None:
    """Exécute le calcul, journalise et persiste le résultat.

    Ne lève jamais — mais ne se tait jamais non plus : tout échec est journalisé et écrit
    dans `ConfigurationAnalyse.execution_erreur`, colonne dédiée pour ne pas écraser les
    messages des étapes précédentes.
    """
    import json

    from app.database.models.resultat_analyse import ResultatAnalyse

    type_analyse = intention.specification.type_analyse
    _log.info("[execution] cfg %s : début (%s)", config.id_configuration, type_analyse)

    try:
        table = prepare.table
        resultat = executer_calcul(intention, table, config.id_configuration)
    except ExecutionError as exc:
        _log.warning("[execution] cfg %s : ECHEC — %s", config.id_configuration, exc.message_complet())
        config.execution_erreur = exc.message_complet()
        db.commit()
        return None
    except Exception as exc:
        _log.exception("[execution] cfg %s : erreur inattendue", config.id_configuration)
        config.execution_erreur = f"Calcul impossible : {exc}"
        db.commit()
        return None

    ligne = ResultatAnalyse(
        id_configuration=config.id_configuration,
        type_analyse=resultat.type_analyse,
        valeur_analyse=str(resultat.valeur_analyse),
        valeur_prevue=None if resultat.valeur_prevue is None else str(resultat.valeur_prevue),
        modele_applique=resultat.modele_applique,
        fiabilite=(resultat.fiabilite or {}).get("code"),
        intervalle_bas=None if resultat.intervalle_bas is None else str(resultat.intervalle_bas),
        intervalle_haut=None if resultat.intervalle_haut is None else str(resultat.intervalle_haut),
        criticite=(resultat.criticite or {}).get("niveau"),
        criticite_rang=(resultat.criticite or {}).get("rang"),
        criticite_motif=(resultat.criticite or {}).get("motif"),
        resultat_json=json.dumps(resultat.resume(), ensure_ascii=False),
    )
    db.add(ligne)
    config.execution_erreur = None
    db.commit()
    resultat.id_resultat = ligne.id_resultat

    # Module 5 — l'alerte est la projection de ce résultat. Branchée ici parce que c'est
    # le seul point où un résultat naît : la placer dans la route laisserait sans alerte
    # tout ce qui passe par un script (relance, recalcul). Import différé pour que le
    # Module 4 ne dépende pas du Module 5 au chargement.
    from app.services.alertes import enregistrer_alerte

    enregistrer_alerte(db, ligne, config)

    _log.info(
        "[execution] cfg %s : OK (%s, modèle=%s) — valeur=%s prévue=%s | criticité=%s — %s",
        config.id_configuration, type_analyse, resultat.modele_applique,
        resultat.valeur_analyse, resultat.valeur_prevue,
        (resultat.criticite or {}).get("niveau"), (resultat.criticite or {}).get("motif"),
    )
    return resultat
