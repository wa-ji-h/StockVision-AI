"""Supervision des configurations d'analyse — vue administrateur.


"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, or_

from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.entreprise import Entreprise
from app.database.models.import_donnee import ImportDonnee
from app.database.models.resultat_analyse import ResultatAnalyse
from app.database.models.source_donnee import SourceDonnee
from app.services.moteur_analyse.criticite import LIBELLE_PAR_NIVEAU, RANG_PAR_NIVEAU
from app.services.supervision import STATUTS_SOURCE_ERREUR

_log = logging.getLogger("stockvision.supervision_analyses")

STATUT_ERREUR = "erreur_donnees"
STATUT_EN_COURS = "extraction_en_cours"

# Statuts d'exécution proposés au filtre. Même vocabulaire que côté entreprise :
# un administrateur et une entreprise qui parlent d'un même état doivent le nommer
# pareil, sinon le support par téléphone devient impossible.
STATUTS_EXECUTION = (
    ("erreur_donnees", "Erreur — données indisponibles", "dash-status-ko"),
    ("extraction_en_cours", "Extraction en cours", "dash-status-info"),
    ("en_attente", "En attente d'exécution", "dash-status-pending"),
    ("en_cours", "En cours", "dash-status-pending"),
    ("non_lancee", "Non lancée", ""),
)
LIBELLE_STATUT = {code: libelle for code, libelle, _ in STATUTS_EXECUTION}
CLASSE_STATUT = {code: classe for code, _, classe in STATUTS_EXECUTION}

FREQUENCES = (("ponctuelle", "Ponctuelle"), ("quotidienne", "Quotidienne"),
              ("hebdomadaire", "Hebdomadaire"), ("mensuelle", "Mensuelle"))
LIBELLE_FREQUENCE = dict(FREQUENCES)

# Nature technique d'un échec — ce que l'administrateur peut effectivement traiter.
# La colonne qui porte l'erreur suffit à la classer : on ne lit jamais son texte.
NATURES_ERREUR = {
    "donnees": ("Source de données indisponible",
                "La source ne répond pas ou son fichier est introuvable. "
                "Vérifiez la source dans la supervision des imports."),
    "traduction": ("Service d'interprétation injoignable",
                   "La traduction de l'intention n'a pas abouti. "
                   "L'analyse retombe sur sa spécification par défaut."),
    "calcul": ("Échec du moteur de calcul",
               "Le calcul n'a pas pu aboutir sur les données préparées."),
}


def nature_erreur(config) -> dict | None:
    """Classe un échec **sans recopier son message**.

    Le message d'exécution est rédigé pour l'entreprise et peut nommer ses
    colonnes : il ne franchit pas la frontière. Seule la **colonne** qui porte
    l'erreur est lue, ce qui suffit à en donner la nature.
    """
    if config.statut_execution == STATUT_ERREUR:
        cle = "donnees"
    elif getattr(config, "execution_erreur", None):
        cle = "calcul"
    elif getattr(config, "intention_erreur", None):
        cle = "traduction"
    else:
        return None
    titre, action = NATURES_ERREUR[cle]
    return {"cle": cle, "titre": titre, "action": action}


def _chaine(requete):
    """Configuration → Import → Source → Entreprise. La seule chaîne d'appartenance."""
    return (requete
            .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
            .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
            .join(Entreprise, SourceDonnee.idEntreprise == Entreprise.idEntreprise))


def indicateurs_parc(db, debut_mois: datetime) -> dict:
    """Les quatre indicateurs de tête. Volumes et états, jamais de contenu."""
    vide = {"actives": 0, "analyses_mois": 0, "en_echec": 0, "programmees": 0}
    try:
        actives = _chaine(db.query(func.count(ConfigurationAnalyse.id_configuration))) \
            .filter(ConfigurationAnalyse.statut == "actif").scalar() or 0
        analyses_mois = (
            _chaine(db.query(func.count(ResultatAnalyse.id_resultat))
                    .join(ConfigurationAnalyse,
                          ResultatAnalyse.id_configuration == ConfigurationAnalyse.id_configuration))
            .filter(ResultatAnalyse.date_execution >= debut_mois).scalar() or 0
        )
        en_echec = _chaine(db.query(func.count(ConfigurationAnalyse.id_configuration))).filter(
            or_(ConfigurationAnalyse.statut_execution == STATUT_ERREUR,
                ConfigurationAnalyse.execution_erreur.isnot(None),
                ConfigurationAnalyse.intention_erreur.isnot(None))).scalar() or 0
        # « Programmée » = récurrente ET portant une prochaine échéance. La colonne
        # `prochaine_execution` existe déjà : rien à calculer ni à stocker.
        programmees = _chaine(db.query(func.count(ConfigurationAnalyse.id_configuration))).filter(
            ConfigurationAnalyse.prochaine_execution.isnot(None),
            ConfigurationAnalyse.frequence != "ponctuelle").scalar() or 0
        return {"actives": actives, "analyses_mois": analyses_mois,
                "en_echec": en_echec, "programmees": programmees}
    except Exception:
        _log.exception("[supervision_analyses] indicateurs indisponibles")
        return vide


def lister_configurations(db, presenter, entreprise_id: int | None = None,
                          statut: str = "tous", frequence: str = "toutes") -> list[dict]:
    """Les configurations du parc, filtrées, avec leurs signaux de supervision.

    `presenter` est `objectif_pour_admin` : la vue en liste blanche qui n'expose
    ni le besoin ni sa reformulation. La passer en argument évite d'importer les
    routes depuis un service.
    """
    try:
        lignes = _chaine(db.query(ConfigurationAnalyse, Entreprise, SourceDonnee)) \
            .order_by(ConfigurationAnalyse.date_creation.desc()).all()
    except Exception:
        _log.exception("[supervision_analyses] liste indisponible")
        return []

    # UNE requête pour tout le parc, quel que soit le nombre de configurations —
    # jamais une requête par ligne. Le « dernier résultat » se prend en parcourant
    # une liste déjà triée : `ORDER BY` dans un `GROUP_CONCAT` serait du SQL propre
    # à MySQL, que rien n'oblige à écrire ici.
    comptes: dict[int, int] = {}
    derniers: dict[int, tuple] = {}
    try:
        for ident, date, criticite in (
            db.query(ResultatAnalyse.id_configuration,
                     ResultatAnalyse.date_execution,
                     ResultatAnalyse.criticite)
            .order_by(ResultatAnalyse.date_execution.desc(),
                      ResultatAnalyse.id_resultat.desc()).all()
        ):
            comptes[ident] = comptes.get(ident, 0) + 1
            derniers.setdefault(ident, (date, criticite))
    except Exception:
        _log.exception("[supervision_analyses] agrégats de résultats indisponibles")

    sorties = []
    for config, entreprise, source in lignes:
        n = comptes.get(config.id_configuration, 0)
        date_dernier, criticite = derniers.get(config.id_configuration, (None, None))
        etat = config.statut_execution or "non_lancee"
        echec = nature_erreur(config)

        ligne = {
            "id": config.id_configuration,
            "id_entreprise": entreprise.idEntreprise,
            "entreprise": entreprise.nom or f"Entreprise {entreprise.idEntreprise}",
            **presenter(config),
            "frequence": config.frequence or "—",
            "frequence_libelle": LIBELLE_FREQUENCE.get(config.frequence, "Non définie"),
            "statut": config.statut,
            "etat": etat,
            "etat_libelle": LIBELLE_STATUT.get(etat, etat),
            "etat_classe": CLASSE_STATUT.get(etat, ""),
            "derniere": config.derniere_execution,
            "prochaine": config.prochaine_execution,
            "creee": config.date_creation,
            "nb_resultats": n,
            "criticite": criticite or "",
            "criticite_libelle": LIBELLE_PAR_NIVEAU.get(criticite, ""),
            "criticite_rang": RANG_PAR_NIVEAU.get(criticite, -1),
            "date_dernier_resultat": date_dernier,
            "source_nom": source.nom or f"Source {source.id_source}",
            "source_en_erreur": source.statut in STATUTS_SOURCE_ERREUR,
            "erreur": echec,
            # ── Signaux de supervision ──────────────────────────────────────
            "jamais_lancee": config.derniere_execution is None and config.statut == "actif",
            "sans_resultat": config.derniere_execution is not None and n == 0,
            "en_echec": echec is not None,
        }
        # Un seul signal prime, le plus actionnable : inutile d'en afficher trois.
        ligne["signal"] = ("en_echec" if ligne["en_echec"]
                           else "source" if ligne["source_en_erreur"]
                           else "sans_resultat" if ligne["sans_resultat"]
                           else "jamais_lancee" if ligne["jamais_lancee"]
                           else "")
        sorties.append(ligne)

    if entreprise_id:
        sorties = [l for l in sorties if l["id_entreprise"] == entreprise_id]
    if statut != "tous":
        sorties = [l for l in sorties if l["etat"] == statut]
    if frequence != "toutes":
        sorties = [l for l in sorties if l["frequence"] == frequence]
    return sorties


def historique_executions(db, id_configuration: int, limite: int = 12) -> list[dict]:
    """Exécutions **ayant produit un résultat**, les plus récentes d'abord.

    ⚠️ Ce n'est pas un journal complet, et le gabarit le dit explicitement : une
    tentative qui échoue ne crée aucune ligne `ResultatAnalyse`. Seul l'état
    d'erreur **courant** est connu, par les colonnes d'erreur de la configuration.
    Présenter cette liste comme exhaustive serait trompeur.
    """
    try:
        lignes = (db.query(ResultatAnalyse)
                  .filter(ResultatAnalyse.id_configuration == id_configuration)
                  .order_by(ResultatAnalyse.date_execution.desc()).limit(limite).all())
    except Exception:
        _log.exception("[supervision_analyses] historique indisponible")
        return []
    return [
        {
            "date": r.date_execution.strftime("%d/%m/%Y à %H:%M") if r.date_execution else "—",
            "criticite": r.criticite or "normal",
            "criticite_libelle": LIBELLE_PAR_NIVEAU.get(r.criticite, "Non évalué"),
            "fiabilite": r.fiabilite or "—",
            # Le modèle appliqué est une information technique, pas un résultat.
            "modele": r.modele_applique or "—",
        }
        for r in lignes
    ]
