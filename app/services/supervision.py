"""Supervision — ce qui appelle l'attention de l'administrateur.

L'administrateur ne suit pas les analyses des entreprises : il veille sur le parc. Ce
module rassemble donc **uniquement des faits déjà produits par l'application**, et chacun
mène à l'écran où il peut agir. Rien n'est inventé, aucun nouvel événement n'est fabriqué.

Trois natures, distinguées par ce que l'administrateur peut en faire :

| Nature        | Ce qu'il fait                                   |
|---------------|-------------------------------------------------|
| `action`      | il tranche — approuver ou refuser une demande    |
| `intervention`| il peut corriger — source en erreur ou suspendue |
| `supervision` | il observe — une alerte critique appartient à l'entreprise |

⚠️ **`supervision` ne donne jamais accès au détail métier.** Une alerte critique se compte
et se situe (quelle entreprise, combien), elle ne se lit pas : le motif appartient à
l'entreprise qui l'a produite.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import case, func, or_

from app.database.models.alerte import Alerte
from app.database.models.resultat_analyse import ResultatAnalyse
from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.entreprise import Entreprise, StatutDemandeEnum
from app.database.models.import_donnee import ImportDonnee
from app.database.models.source_donnee import SourceDonnee
from app.services.alertes import STATUT_TRAITEE
from app.services.moteur_analyse.criticite import RANG_PAR_NIVEAU

_log = logging.getLogger("stockvision.supervision")

RANG_CRITIQUE = RANG_PAR_NIVEAU["critique"]

# Nature → (libellé, ton). Le ton pilote la couleur, sans en introduire de nouvelle :
# `action` reprend l'accent, `intervention` l'orange d'« élevé », `supervision` le rouge
# de « critique ». Le vert reste réservé aux confirmations.
NATURES = {
    "action": ("Action requise", "accent"),
    "intervention": ("Intervention possible", "eleve"),
    "supervision": ("À surveiller", "critique"),
}


def _demandes_en_attente(db) -> dict | None:
    n = db.query(Entreprise).filter(
        Entreprise.statut_demande == StatutDemandeEnum.en_attente).count()
    if not n:
        return None
    return {
        "cle": "demandes",
        "nature": "action",
        "nombre": n,
        "titre": f"{n} demande{'s' if n > 1 else ''} d'inscription en attente",
        "detail": "Une entreprise ne peut pas se connecter tant que sa demande n'est pas tranchée.",
        "lien": "/dashboard/admin/demandes",
        "action": "Traiter les demandes",
    }


def _sources_en_difficulte(db) -> dict | None:
    n = db.query(SourceDonnee).filter(
        or_(SourceDonnee.statut == "erreur", SourceDonnee.statut == "suspendu")).count()
    if not n:
        return None
    return {
        "cle": "sources",
        "nature": "intervention",
        "nombre": n,
        "titre": f"{n} source{'s' if n > 1 else ''} en erreur ou suspendue{'s' if n > 1 else ''}",
        "detail": "Les analyses qui en dépendent ne peuvent plus s'exécuter.",
        "lien": "/dashboard/admin/imports",
        "action": "Voir les sources",
    }


def _alertes_critiques(db) -> dict | None:
    n = (db.query(Alerte)
           .filter(Alerte.criticite_rang >= RANG_CRITIQUE)
           .filter(Alerte.statut != STATUT_TRAITEE).count())
    if not n:
        return None
    return {
        "cle": "alertes",
        "nature": "supervision",
        "nombre": n,
        "titre": f"{n} alerte{'s' if n > 1 else ''} critique{'s' if n > 1 else ''} non traitée{'s' if n > 1 else ''}",
        # Le rôle est dit explicitement : sans cela, un administrateur pourrait croire
        # qu'on attend de lui qu'il agisse sur l'analyse d'une entreprise.
        "detail": "Le traitement appartient aux entreprises concernées — vous en suivez le volume.",
        "lien": "/dashboard/admin/alertes",
        "action": "Voir la répartition",
    }


def notifications_admin(db) -> list[dict]:
    """Ce qui appelle l'attention, le plus engageant d'abord.

    Ne lève jamais : une supervision indisponible ne doit pas empêcher une page de
    s'afficher.
    """
    try:
        items = [f(db) for f in (_demandes_en_attente, _sources_en_difficulte, _alertes_critiques)]
        return [i for i in items if i]
    except Exception:
        _log.exception("[supervision] agrégation impossible")
        return []


def total_notifications(db) -> int:
    """Le nombre porté par le badge : la somme de ce qui reste à regarder."""
    return sum(i["nombre"] for i in notifications_admin(db))


# Deux actions de même nature, par la même entreprise, séparées de moins de ça sont un
# même geste : créer trois configurations à la suite est UNE session de travail, pas trois
# événements. Trois lignes identiques à la même minute n'informaient de rien.
FENETRE_REGROUPEMENT = timedelta(minutes=15)


def _libelle_activite(quoi: str, nombre: int, cible: str | None) -> str:
    """Formulation au singulier ou au pluriel, selon ce qui a réellement été fait."""
    if quoi == "import":
        if nombre == 1:
            return f"a importé « {cible} »" if cible else "a importé un fichier"
        return f"a importé {nombre} fichiers"
    if nombre == 1:
        return "a créé une configuration d'analyse"
    return f"a créé {nombre} configurations d'analyse"


def activite_recente(db, limite: int = 6) -> list[dict]:
    """Dernières actions des entreprises — le fil de vie du parc, **regroupé**.

    Uniquement des faits déjà enregistrés : un import déposé, une configuration créée.
    Aucun contenu métier, seulement qui a fait quoi et quand.

    Les actions identiques et rapprochées d'une même entreprise sont fusionnées en une
    ligne portant leur nombre et leur plage horaire. On lit alors une activité, pas une
    répétition.
    """
    try:
        brut: list[dict] = []
        # On lit large avant de regrouper : sinon un lot de cinq imports consommerait
        # toute la place et masquerait le reste de l'activité du parc.
        marge = max(limite * 5, 25)

        imports = (
            db.query(ImportDonnee, SourceDonnee, Entreprise)
            .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
            .join(Entreprise, SourceDonnee.idEntreprise == Entreprise.idEntreprise)
            .order_by(ImportDonnee.date_import.desc())
            .limit(marge).all()
        )
        for imp, src, ent in imports:
            brut.append({
                "quoi": "import",
                "entreprise": ent.nom or f"Entreprise {ent.idEntreprise}",
                "cible": imp.nom_fichier or src.nom,
                "date": imp.date_import,
            })

        configs = (
            db.query(ConfigurationAnalyse, Entreprise)
            .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
            .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
            .join(Entreprise, SourceDonnee.idEntreprise == Entreprise.idEntreprise)
            .order_by(ConfigurationAnalyse.date_creation.desc())
            .limit(marge).all()
        )
        for cfg, ent in configs:
            brut.append({
                "quoi": "configuration",
                "entreprise": ent.nom or f"Entreprise {ent.idEntreprise}",
                "cible": None,
                "date": cfg.date_creation,
            })

        brut = [b for b in brut if b["date"]]
        brut.sort(key=lambda b: b["date"], reverse=True)

        groupes: list[dict] = []
        for evenement in brut:
            dernier = groupes[-1] if groupes else None
            # Le fil est en ordre décroissant : `debut` est la borne la plus ancienne du
            # groupe courant, c'est donc à elle qu'on compare l'événement suivant.
            proche = (
                dernier is not None
                and dernier["quoi"] == evenement["quoi"]
                and dernier["entreprise"] == evenement["entreprise"]
                and dernier["debut"] - evenement["date"] <= FENETRE_REGROUPEMENT
            )
            if proche:
                dernier["nombre"] += 1
                dernier["debut"] = evenement["date"]
                continue
            if len(groupes) >= limite:
                break
            groupes.append({
                "quoi": evenement["quoi"],
                "entreprise": evenement["entreprise"],
                "cible": evenement["cible"],
                "nombre": 1,
                "debut": evenement["date"],
                "fin": evenement["date"],
            })

        for g in groupes:
            g["libelle"] = _libelle_activite(g["quoi"], g["nombre"], g["cible"])
        return groupes
    except Exception:
        _log.exception("[supervision] activité récente indisponible")
        return []


# Une source dans l'un de ces états n'alimente plus aucune analyse : c'est le seul
# décompte de cette page qui appelle une intervention.
STATUTS_SOURCE_ERREUR = ("erreur", "suspendu")


def _cle_entreprise(rangees) -> dict[int, tuple]:
    """Indexe des lignes agrégées par identifiant d'entreprise (1re colonne)."""
    return {r[0]: tuple(r[1:]) for r in rangees}


def activite_par_entreprise(db, debut_mois=None) -> dict[int, dict]:
    """Volumes d'activité de **toutes** les entreprises, en 4 requêtes agrégées.

    ⚠️ **Le nombre de requêtes ne dépend pas du nombre d'entreprises** : chaque décompte
    est un `GROUP BY idEntreprise` sur l'ensemble du parc, jamais une requête par ligne.
    C'est ce qui rend la page tenable quand le parc grandit — un décompte par entreprise
    aurait donné 4 × N requêtes.

    Rien n'est créé ni stocké : tout se déduit de `SourceDonnee`, `ImportDonnee`,
    `ConfigurationAnalyse`, `ResultatAnalyse` et `Alerte`.
    """
    vide = {
        "sources": 0, "sources_erreur": 0,
        "analyses": 0, "analyses_mois": 0,
        "alertes": 0, "alertes_critiques": 0,
        "derniere_activite": None,
    }
    try:
        # La chaîne d'appartenance est la même partout : Résultat/Alerte → Configuration
        # → Import → Source → Entreprise. Aucune autre ne relie une analyse à son
        # propriétaire.
        def remonter(requete):
            return (requete
                    .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
                    .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source))

        erreur = func.sum(
            case((SourceDonnee.statut.in_(STATUTS_SOURCE_ERREUR), 1), else_=0))
        sources = _cle_entreprise(
            db.query(SourceDonnee.idEntreprise, func.count(SourceDonnee.id_source), erreur)
              .group_by(SourceDonnee.idEntreprise).all())

        imports = _cle_entreprise(
            db.query(SourceDonnee.idEntreprise, func.max(ImportDonnee.date_import))
              .join(ImportDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
              .group_by(SourceDonnee.idEntreprise).all())

        du_mois = func.sum(
            case((ResultatAnalyse.date_execution >= debut_mois, 1), else_=0)
        ) if debut_mois else func.count(ResultatAnalyse.id_resultat)
        analyses = _cle_entreprise(
            remonter(db.query(SourceDonnee.idEntreprise,
                              func.count(ResultatAnalyse.id_resultat),
                              du_mois,
                              func.max(ResultatAnalyse.date_execution))
                       .join(ConfigurationAnalyse,
                             ResultatAnalyse.id_configuration == ConfigurationAnalyse.id_configuration))
            .group_by(SourceDonnee.idEntreprise).all())

        critiques = func.sum(case((Alerte.criticite_rang >= RANG_CRITIQUE, 1), else_=0))
        alertes = _cle_entreprise(
            remonter(db.query(SourceDonnee.idEntreprise,
                              func.count(Alerte.id_alerte), critiques)
                       .join(ConfigurationAnalyse,
                             Alerte.id_configuration == ConfigurationAnalyse.id_configuration))
            .filter(Alerte.statut != STATUT_TRAITEE)
            .group_by(SourceDonnee.idEntreprise).all())

        volumes: dict[int, dict] = {}
        for ident in set(sources) | set(imports) | set(analyses) | set(alertes):
            n_src, n_err = sources.get(ident, (0, 0))
            (dernier_import,) = imports.get(ident, (None,))
            n_ana, n_mois, derniere_exec = analyses.get(ident, (0, 0, None))
            n_alr, n_crit = alertes.get(ident, (0, 0))
            # Deux traces d'activité, on retient la plus récente : une entreprise qui
            # relance une analyse sans réimporter est active, et réciproquement.
            dates = [d for d in (dernier_import, derniere_exec) if d]
            volumes[ident] = {
                "sources": int(n_src or 0), "sources_erreur": int(n_err or 0),
                "analyses": int(n_ana or 0), "analyses_mois": int(n_mois or 0),
                "alertes": int(n_alr or 0), "alertes_critiques": int(n_crit or 0),
                "derniere_activite": max(dates) if dates else None,
            }
        return {"_vide": vide, **volumes}
    except Exception:
        _log.exception("[supervision] volumes par entreprise indisponibles")
        return {"_vide": vide}


def repartition_alertes(db) -> dict:
    """Volumes d'alertes du parc, pour les tuiles de tête de la page admin."""
    total, non_traitees = 0, 0
    par_niveau: dict[str, int] = {}
    for niveau, statut, n in (db.query(Alerte.niveau, Alerte.statut, func.count(Alerte.id_alerte))
                                .group_by(Alerte.niveau, Alerte.statut).all()):
        total += n
        if statut != STATUT_TRAITEE:
            non_traitees += n
        par_niveau[niveau] = par_niveau.get(niveau, 0) + n
    return {"total": total, "non_traitees": non_traitees, "par_niveau": par_niveau}
