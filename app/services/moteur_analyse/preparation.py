"""Module 4 — tâche 4.2 : préparation des données.

Prend le `ResultatExtraction` produit par 4.1 et rend une structure prête pour le modèle
statistique de 4.3. **Aucun modèle n'est appelé ici** : on nettoie, on normalise, on agrège.

Travail exclusivement sur copie en mémoire : ni les fichiers de `app/uploads/`, ni les bases
tierces ne sont touchés — aucune écriture n'est émise vers une source.

La détection de la colonne temporelle est déléguée à `extraction.colonnes_temporelles`, la même
que celle du profilage du wizard : ce que le wizard annonce comme compatible est donc exactement
ce que la préparation acceptera.

Sortie consommable telle quelle par 4.3 : `ResultatPreparation.table.donnees` est un DataFrame
indexé par le temps (quand il y a agrégation), trié, sans ligne à date manquante.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.services.moteur_analyse.extraction import (
    ExtractionError,
    ResultatExtraction,
    colonnes_temporelles,
)
from app.services.moteur_analyse.fiabilite import (
    PAS_PAR_FREQUENCE,
    evaluer_fiabilite,
    phrase_fiabilite,
)

# Objectifs qui exigent une colonne temporelle exploitable ET une mesure numérique.
OBJECTIFS_TEMPORELS = frozenset({"prevision_evolution", "prevision_tendance"})


class PreparationError(ExtractionError):
    """Erreur métier de préparation.

    Hérite d'`ExtractionError` pour garder un contrat de message identique (`.message`,
    `.message_complet()`) et rester rattrapable par les appelants du moteur qui traitent
    déjà les erreurs de 4.1.
    """


@dataclass
class TablePreparee:
    """Une table prête pour le modèle."""

    nom: str
    donnees: pd.DataFrame
    colonne_temps: str | None
    pas: str | None                     # 'D' | 'W' | 'MS' | None
    pas_label: str | None               # 'jour' | 'semaine' | 'mois' | None
    colonnes_valeurs: list[str]         # colonnes numériques exploitables par 4.3
    colonnes_categorielles: list[str]   # dimensions de regroupement (comparaison/classement)
    n_points: int                       # points réellement porteurs de données
    n_periodes: int                     # longueur de la grille (agrégation) ou = n_points
    n_lignes_source: int
    fiabilite: dict = field(default_factory=dict)   # {code, label, nuance, seuil, points}
    avertissements: list[str] = field(default_factory=list)


@dataclass
class ResultatPreparation:
    """Sortie de 4.2, entrée de 4.3."""

    config_id: int
    objectif: str
    frequence: str | None
    tables: dict[str, TablePreparee]
    avertissements: list[str] = field(default_factory=list)

    @property
    def table(self) -> TablePreparee:
        """La table unique (cas courant). Avec plusieurs tables, 4.3 pioche dans `tables`."""
        if len(self.tables) == 1:
            return next(iter(self.tables.values()))
        raise PreparationError(
            f"Cette configuration porte sur {len(self.tables)} tables "
            f"({', '.join(sorted(self.tables))}) : précisez laquelle analyser."
        )

    def resume(self) -> dict:
        return {
            "config_id": self.config_id,
            "objectif": self.objectif,
            "frequence": self.frequence,
            "fiabilite": self.fiabilite,
            "tables": {
                nom: {
                    "colonne_temps": t.colonne_temps,
                    "pas": t.pas_label,
                    "points": t.n_points,
                    "periodes": t.n_periodes,
                    "lignes_source": t.n_lignes_source,
                    "fiabilite": t.fiabilite,
                    "colonnes_valeurs": t.colonnes_valeurs,
                    "colonnes_categorielles": t.colonnes_categorielles,
                }
                for nom, t in self.tables.items()
            },
            "avertissements": self.avertissements,
        }

    @property
    def fiabilite(self) -> dict:
        """Fiabilité de l'analyse : celle de la table la moins fournie."""
        if not self.tables:
            return evaluer_fiabilite(0)
        return min(
            (t.fiabilite for t in self.tables.values()),
            key=lambda f: f.get("points", 0),
        )

    def message(self) -> str:
        """Résumé destiné à `ConfigurationAnalyse.message_execution`.

        Constate la fiabilité, n'annonce jamais un rejet pour volume : à ce stade
        l'analyse a déjà été préparée avec succès.
        """
        morceaux = []
        for nom, t in self.tables.items():
            temps = f"colonne temporelle « {t.colonne_temps} »" if t.colonne_temps else "sans colonne temporelle"
            pas = f"agrégation par {t.pas_label}" if t.pas_label else "sans agrégation"
            morceaux.append(f"« {nom} » : {temps}, {pas}")
        texte = "Préparation réussie — " + " ; ".join(morceaux) + ". "
        texte += phrase_fiabilite(self.fiabilite["points"])
        if self.avertissements:
            texte += " " + " ".join(self.avertissements)
        return texte


# ──────────────────────────────────────────────────────────────────────────────
# Étapes de préparation
# ──────────────────────────────────────────────────────────────────────────────

def _choisir_colonne_temps(df: pd.DataFrame) -> str | None:
    """Colonne temporelle retenue : la mieux renseignée parmi celles détectées.

    Un fichier peut porter plusieurs dates (création, mise à jour, livraison…) ; celle qui
    a le moins de valeurs manquantes est la plus exploitable pour une série.
    """
    candidates = colonnes_temporelles(df)
    if not candidates:
        return None
    return max(candidates, key=lambda c: int(df[c].notna().sum()))


def _nettoyer(df: pd.DataFrame, colonne_temps: str | None, nom: str) -> tuple[pd.DataFrame, list[str]]:
    """Valeurs manquantes et normalisation des dates, sur une copie."""
    avertissements: list[str] = []
    df = df.copy()

    vides = [c for c in df.columns if df[c].isna().all()]
    if vides:
        df = df.drop(columns=vides)
        avertissements.append(
            f"Colonne(s) entièrement vide(s) écartée(s) de « {nom} » : {', '.join(map(str, vides))}."
        )

    if colonne_temps and colonne_temps in df.columns:
        # Une ligne sans date ne peut être placée sur l'axe du temps.
        sans_date = int(df[colonne_temps].isna().sum())
        if sans_date:
            df = df[df[colonne_temps].notna()]
            avertissements.append(
                f"{sans_date} ligne(s) sans date écartée(s) de « {nom} »."
            )
        # Normalisation : fuseau retiré pour un axe temporel homogène, puis tri chronologique.
        serie = df[colonne_temps]
        if isinstance(serie.dtype, pd.DatetimeTZDtype):
            df[colonne_temps] = serie.dt.tz_localize(None)
            avertissements.append(f"Fuseau horaire retiré de « {colonne_temps} » pour homogénéiser l'axe du temps.")
        df = df.sort_values(colonne_temps)

    return df, avertissements


def _agreger(df: pd.DataFrame, colonne_temps: str, pas: str, pas_label: str, nom: str) -> tuple[pd.DataFrame, list[str]]:
    """Rééchantillonne au pas demandé. Les colonnes numériques sont sommées.

    `min_count=1` garantit qu'une période sans donnée reste `NaN` plutôt que de devenir un
    faux zéro. La grille régulière est conservée (4.3 en a besoin pour une série temporelle),
    mais seules les périodes réellement porteuses de données comptent comme points.
    """
    avertissements: list[str] = []
    indexe = df.set_index(colonne_temps)

    numeriques = [c for c in indexe.columns if pd.api.types.is_numeric_dtype(indexe[c])]
    if not numeriques:
        raise PreparationError(
            f"Aucune colonne numérique à agréger dans « {nom} » : "
            "sélectionnez au moins une mesure chiffrée dans la configuration."
        )

    agrege = indexe[numeriques].resample(pas).sum(min_count=1)
    agrege["_n_lignes"] = indexe.resample(pas).size()

    non_vides = int((agrege["_n_lignes"] > 0).sum())
    creux = len(agrege) - non_vides
    if creux:
        avertissements.append(
            f"{creux} {pas_label}(s) sans donnée dans « {nom} » : la grille est conservée, "
            "ces périodes restent vides."
        )
    return agrege, avertissements


def _preparer_table(
    nom: str,
    df: pd.DataFrame,
    objectif: str,
    frequence: str | None,
) -> TablePreparee:
    n_source = len(df)
    temporel = objectif in OBJECTIFS_TEMPORELS

    colonne_temps = _choisir_colonne_temps(df)
    if temporel and not colonne_temps:
        raise PreparationError(
            f"Aucune colonne de date exploitable dans « {nom} » : l'objectif choisi en exige une. "
            "Sélectionnez une colonne de date dans la configuration, ou choisissez un objectif "
            "qui n'en demande pas (Identifier une anomalie, Comparer et classer)."
        )

    df, avertissements = _nettoyer(df, colonne_temps, nom)
    if df.empty:
        raise PreparationError(f"Plus aucune donnée exploitable dans « {nom} » après nettoyage.")

    # L'agrégation temporelle n'a de sens que pour un objectif temporel : pour un
    # classement, elle détruirait la granularité par produit/client qui fait l'analyse.
    pas = pas_label = None
    if temporel and colonne_temps and frequence in PAS_PAR_FREQUENCE:
        reglage = PAS_PAR_FREQUENCE[frequence]
        pas, pas_label = reglage["resample"], reglage["label"]
        df, avert_agreg = _agreger(df, colonne_temps, pas, pas_label, nom)
        avertissements += avert_agreg
        n_points = int((df["_n_lignes"] > 0).sum())
        n_periodes = len(df)
    else:
        if temporel and colonne_temps and frequence not in PAS_PAR_FREQUENCE:
            avertissements.append(
                f"Fréquence « {frequence or 'ponctuelle'} » : analyse sur les données brutes, sans agrégation."
            )
        if colonne_temps:
            df = df.set_index(colonne_temps)
        n_points = n_periodes = len(df)

    # Le volume ne bloque JAMAIS : il qualifie la fiabilité du résultat. Seule
    # l'impossibilité mathématique (traitée plus haut) interrompt une analyse.
    fiabilite = evaluer_fiabilite(n_points)

    valeurs = [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c != "_n_lignes"]
    categorielles = [str(c) for c in df.columns if str(c) not in valeurs and c != "_n_lignes"]

    return TablePreparee(
        nom=nom,
        donnees=df,
        colonne_temps=colonne_temps,
        pas=pas,
        pas_label=pas_label,
        colonnes_valeurs=valeurs,
        colonnes_categorielles=categorielles,
        n_points=n_points,
        n_periodes=n_periodes,
        n_lignes_source=n_source,
        fiabilite=fiabilite,
        avertissements=avertissements,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────────────

def preparer_donnees(resultat: ResultatExtraction, config) -> ResultatPreparation:
    """Prépare les données extraites par 4.1 pour le modèle de 4.3.

    Lève `PreparationError` (message en français) pour tout échec métier.
    """
    if not resultat.donnees:
        raise PreparationError("Aucune donnée à préparer : l'extraction n'a rien renvoyé.")

    objectif = config.objectif or ""
    tables: dict[str, TablePreparee] = {}
    avertissements: list[str] = []
    for nom, df in resultat.donnees.items():
        preparee = _preparer_table(nom, df, objectif, config.frequence)
        tables[nom] = preparee
        avertissements += preparee.avertissements

    return ResultatPreparation(
        config_id=config.id_configuration,
        objectif=objectif,
        frequence=config.frequence,
        tables=tables,
        avertissements=avertissements,
    )


def executer_preparation(db, config, resultat: ResultatExtraction) -> ResultatPreparation | None:
    """Enveloppe `preparer_donnees` avec le suivi d'état en base.

    Retourne `None` en cas d'échec — le message est alors dans `config.message_execution`
    et le statut passe en erreur. Ne lève jamais.
    """
    from app.services.moteur_analyse.extraction import STATUT_ERREUR_DONNEES

    try:
        prepare = preparer_donnees(resultat, config)
    except ExtractionError as exc:  # couvre PreparationError
        config.statut_execution = STATUT_ERREUR_DONNEES
        config.message_execution = exc.message_complet()
        db.commit()
        return None
    except Exception as exc:  # filet : aucune exception brute ne doit remonter
        config.statut_execution = STATUT_ERREUR_DONNEES
        config.message_execution = f"Erreur inattendue pendant la préparation : {exc}"
        db.commit()
        return None

    # 4.3 n'existe pas encore : la configuration reste en attente du modèle.
    config.statut_execution = "en_attente"
    config.message_execution = prepare.message()
    # Conservé en base pour rester affichable sur la page des résultats, pas seulement
    # dans la réponse au lancement.
    config.fiabilite_execution = prepare.fiabilite["code"]
    config.points_execution = prepare.fiabilite["points"]
    db.commit()
    return prepare
