"""Module 5 — alertes.

Une alerte est la **projection d'un résultat critique**, rien de plus : elle ne calcule
rien, ne reformule rien, n'appelle aucun modèle de langage. Le Module 4 a déjà produit le
niveau (`criticite`), son rang et sa justification (`criticite_motif`) ; ce module se
contente de les rendre consultables et actionnables.

Seuil de déclenchement : `criticite_rang >= 2` (élevé ou critique). Les rangs 0 et 1 ne
créent rien — alerter sur une variation mineure noierait l'entreprise sous les
notifications, et une alerte qu'on n'ouvre plus ne vaut rien.

⚠️ **Relation : Résultat → Alerte.** Un résultat produit au plus une alerte ; relancer une
configuration ne fabrique pas de doublon. Si le rang change (recalcul de criticité, seuils
modifiés), l'alerte existante est mise à jour pour refléter le verdict courant plutôt que
de rester figée sur un verdict périmé.

Ne dépend que de `criticite.py` côté Module 4 — module feuille, aucun cycle possible.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, text

from app.database.models.alerte import Alerte
from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.import_donnee import ImportDonnee
from app.database.models.resultat_analyse import ResultatAnalyse
from app.database.models.source_donnee import SourceDonnee
from app.services.moteur_analyse.criticite import (
    LIBELLE_PAR_NIVEAU,
    NIVEAUX_CRITICITE,
    RANG_PAR_NIVEAU,
)

_log = logging.getLogger("stockvision.alertes")

# Seuil dérivé, jamais recopié : déplacer « élevé » dans criticite.py déplace le
# déclenchement avec lui.
SEUIL_ALERTE = RANG_PAR_NIVEAU["eleve"]

# Niveaux qui peuvent donner lieu à une alerte — les seuls affichés dans les filtres.
NIVEAUX_ALERTE = [
    {"code": code, "rang": rang, "libelle": libelle}
    for code, rang, libelle in NIVEAUX_CRITICITE
    if rang >= SEUIL_ALERTE
]

STATUT_NOUVELLE = "nouvelle"
STATUT_TRAITEE = "traitee"


def merite_alerte(rang: int | None) -> bool:
    """Un résultat mérite-t-il une alerte ? Unique juge de cette question."""
    return rang is not None and rang >= SEUIL_ALERTE


def enregistrer_alerte(db, ligne: ResultatAnalyse, config: ConfigurationAnalyse) -> Alerte | None:
    """Crée, met à jour ou retire l'alerte d'un résultat. **Ne lève jamais.**

    Une alerte manquante ne doit pas faire échouer une analyse par ailleurs réussie :
    l'échec est journalisé, le résultat reste écrit.
    """
    try:
        existante = db.query(Alerte).filter(Alerte.id_resultat == ligne.id_resultat).first()

        if not merite_alerte(ligne.criticite_rang):
            # Le résultat est repassé sous le seuil (recalcul, seuils ajustés) : l'alerte
            # n'a plus lieu d'être. La laisser afficherait un verdict que le calcul ne
            # soutient plus.
            if existante:
                db.delete(existante)
                db.commit()
                _log.info("[alertes] résultat %s repassé sous le seuil : alerte retirée",
                          ligne.id_resultat)
            return None

        if existante:
            change = (existante.criticite_rang != ligne.criticite_rang
                      or existante.message != ligne.criticite_motif)
            if change:
                existante.niveau = ligne.criticite
                existante.criticite_rang = ligne.criticite_rang
                existante.message = ligne.criticite_motif
                existante.type_alerte = ligne.type_analyse
                db.commit()
                _log.info("[alertes] alerte %s mise à jour (rang %s)",
                          existante.id_alerte, ligne.criticite_rang)
            return existante

        alerte = Alerte(
            type_alerte=ligne.type_analyse,
            # Le motif de criticité fait office de message, sans réécriture : c'est lui
            # qui explique le déclenchement, et il a été produit de façon déterministe.
            message=ligne.criticite_motif,
            niveau=ligne.criticite,
            criticite_rang=ligne.criticite_rang,
            id_resultat=ligne.id_resultat,
            id_configuration=config.id_configuration,
            statut=STATUT_NOUVELLE,
        )
        db.add(alerte)
        db.commit()
        _log.info("[alertes] alerte créée pour le résultat %s (cfg %s, niveau %s)",
                  ligne.id_resultat, config.id_configuration, ligne.criticite)
        return alerte
    except Exception as exc:
        db.rollback()
        _log.exception("[alertes] échec d'enregistrement pour le résultat %s : %s",
                       ligne.id_resultat, exc)
        return None


def _alertes_de_lentreprise(db, id_entreprise: int):
    """Requête de base : les alertes des configurations de cette entreprise.

    Le chemin passe par l'import puis la source — c'est la source qui porte le
    propriétaire. Sans cette jointure, une entreprise verrait les alertes des autres.
    """
    return (
        db.query(Alerte)
        .join(ConfigurationAnalyse,
              Alerte.id_configuration == ConfigurationAnalyse.id_configuration)
        .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .filter(SourceDonnee.idEntreprise == id_entreprise)
    )


def compter_non_lues(db, id_entreprise: int) -> int:
    """Nombre d'alertes non traitées — alimente le badge du topbar.

    Appelée au rendu de chaque page du dashboard : elle doit rester bon marché, d'où
    l'index sur (statut, date_creation).
    """
    try:
        return (_alertes_de_lentreprise(db, id_entreprise)
                .filter(Alerte.statut != STATUT_TRAITEE).count())
    except Exception:
        # Un badge indisponible ne doit jamais empêcher une page de s'afficher.
        _log.exception("[alertes] décompte impossible pour l'entreprise %s", id_entreprise)
        return 0


def alertes_recentes(db, id_entreprise: int, limite: int = 5) -> list[Alerte]:
    """Les non traitées les plus récentes, pour le panneau du topbar."""
    return (_alertes_de_lentreprise(db, id_entreprise)
            .filter(Alerte.statut != STATUT_TRAITEE)
            .order_by(Alerte.date_creation.desc(), Alerte.id_alerte.desc())
            .limit(limite).all())


def lister_alertes(db, id_entreprise: int, niveau: str = "tous") -> list[Alerte]:
    """Toutes les alertes de l'entreprise, les plus récentes d'abord.

    Les traitées restent listées : elles sortent du décompte, pas de l'historique.
    """
    requete = _alertes_de_lentreprise(db, id_entreprise)
    if niveau != "tous" and niveau in {n["code"] for n in NIVEAUX_ALERTE}:
        requete = requete.filter(Alerte.niveau == niveau)
    return requete.order_by(Alerte.date_creation.desc(), Alerte.id_alerte.desc()).all()


def compter_par_niveau(db, id_entreprise: int) -> dict[str, int]:
    """Comptes par niveau, calculés **avant** tout filtrage : un filtre doit annoncer
    ce qu'il cache."""
    comptes = {n["code"]: 0 for n in NIVEAUX_ALERTE}
    for niveau, n in (_alertes_de_lentreprise(db, id_entreprise)
                      .with_entities(Alerte.niveau, func.count(Alerte.id_alerte))
                      .group_by(Alerte.niveau).all()):
        if niveau in comptes:
            comptes[niveau] = n
    return comptes


def marquer_traitee(db, id_alerte: int, id_entreprise: int, traitee: bool = True) -> bool:
    """Marque une alerte traitée — ou la remet en non lue. Renvoie False si elle
    n'appartient pas à cette entreprise (le filtre de propriété est dans la requête)."""
    alerte = _alertes_de_lentreprise(db, id_entreprise).filter(
        Alerte.id_alerte == id_alerte).first()
    if not alerte:
        return False
    alerte.statut = STATUT_TRAITEE if traitee else STATUT_NOUVELLE
    alerte.date_traitement = datetime.now() if traitee else None
    db.commit()
    return True


def synthese_admin(db) -> list[dict]:
    """Vue de supervision : volumes par entreprise et par niveau.

    ⚠️ **Aucun message, aucun motif, aucun accès au résultat.** L'administrateur
    supervise la charge d'alertes, il n'agit pas à la place des entreprises et n'a pas à
    connaître le détail métier de leurs analyses.
    """
    from app.database.models.entreprise import Entreprise

    lignes = (
        db.query(SourceDonnee.idEntreprise, Alerte.niveau, Alerte.statut,
                 func.count(Alerte.id_alerte))
        .join(ConfigurationAnalyse,
              Alerte.id_configuration == ConfigurationAnalyse.id_configuration)
        .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .group_by(SourceDonnee.idEntreprise, Alerte.niveau, Alerte.statut)
        .all()
    )
    # Les DATES, dans une seconde requête agrégée : deux horodatages suffisent à
    # distinguer une entreprise réactive d'une qui ignore ses alertes. Ni message,
    # ni motif, ni résultat n'est lu — seulement `date_creation` et `date_traitement`.
    delais = (
        db.query(SourceDonnee.idEntreprise, Alerte.statut,
                 func.min(Alerte.date_creation), func.count(Alerte.id_alerte),
                 func.sum(func.timestampdiff(
                     text("SECOND"), Alerte.date_creation, Alerte.date_traitement)))
        .join(ConfigurationAnalyse,
              Alerte.id_configuration == ConfigurationAnalyse.id_configuration)
        .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .group_by(SourceDonnee.idEntreprise, Alerte.statut)
        .all()
    )
    if not lignes:
        return []

    ids = {l[0] for l in lignes}
    noms = {
        e.idEntreprise: (e.nom or f"Entreprise {e.idEntreprise}")
        for e in db.query(Entreprise).filter(Entreprise.idEntreprise.in_(ids)).all()
    }

    par_entreprise: dict[int, dict] = {}
    for id_ent, niveau, statut, n in lignes:
        e = par_entreprise.setdefault(id_ent, {
            "id_entreprise": id_ent,
            "nom": noms.get(id_ent, f"Entreprise {id_ent}"),
            "total": 0, "non_traitees": 0,
            # Renseignées plus bas si les dates le permettent ; jamais inventées.
            "plus_ancienne": None, "anciennete_jours": None,
            "delai_moyen_h": None, "nb_traitees": 0,
            **{niv["code"]: 0 for niv in NIVEAUX_ALERTE},
        })
        e["total"] += n
        if statut != STATUT_TRAITEE:
            e["non_traitees"] += n
        if niveau in e:
            e[niveau] += n

    maintenant = datetime.now()
    for id_ent, statut, plus_ancienne, n, somme_secondes in delais:
        e = par_entreprise.get(id_ent)
        if e is None:
            continue
        if statut != STATUT_TRAITEE:
            # Ancienneté de la plus ancienne alerte ENCORE ouverte : une alerte
            # critique laissée trois semaines n'est pas un incident de même nature
            # qu'une alerte ouverte il y a une heure.
            e["plus_ancienne"] = plus_ancienne
            e["anciennete_jours"] = ((maintenant - plus_ancienne).days
                                     if plus_ancienne else None)
        elif n:
            # Délai moyen de traitement, sur les seules alertes effectivement traitées.
            e["delai_moyen_h"] = round(float(somme_secondes or 0) / n / 3600, 1)
            e["nb_traitees"] = n

    # Trié par ce qui appelle le plus l'attention, jamais par ordre alphabétique :
    # d'abord les critiques ouvertes, puis l'ancienneté du plus vieux dossier, puis
    # le volume. Le nom ne sert qu'à départager deux situations identiques.
    return sorted(
        par_entreprise.values(),
        key=lambda e: (-e.get("critique", 0), -(e.get("anciennete_jours") or -1),
                       -e["non_traitees"], -e["total"], e["nom"]))


def libelle_niveau(code: str) -> str:
    return LIBELLE_PAR_NIVEAU.get(code, code or "—")
