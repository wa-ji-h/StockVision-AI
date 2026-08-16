"""Séries temporelles et répartitions des deux tableaux de bord.

⚠️ **Rien n'est stocké, aucune table ni colonne ajoutée.** Chaque série est un
`GROUP BY` sur une date déjà présente au modèle :

| Série | Colonne lue |
|---|---|
| imports | `ImportDonnee.date_import` |
| analyses lancées | `ResultatAnalyse.date_execution` |
| alertes déclenchées | `Alerte.date_creation` |
| sources déclarées | `SourceDonnee.date_creation` |
| inscriptions (admin) | `Entreprise.date_inscription` |

⚠️ **Une seule série est affichée à la fois** dans les graphiques à sélecteur : la
courbe porte donc l'accent de la marque, et aucune palette catégorielle n'est en jeu.
Deux séries superposées auraient exigé deux teintes distinguables, que le design
system ne fournit pas ici.

**Aucun pourcentage de variation n'est produit.** L'historique disponible ne couvre
pas deux périodes comparables : un delta serait inventé.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_

from app.database.models.alerte import Alerte
from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.entreprise import Entreprise
from app.database.models.import_donnee import ImportDonnee
from app.database.models.resultat_analyse import ResultatAnalyse
from app.database.models.source_donnee import SourceDonnee

_log = logging.getLogger("stockvision.graphiques")

# Fenêtre d'observation. 30 jours est ce que demande la lecture « tendance récente » ;
# les jours sans événement valent 0, ils ne sont jamais omis — un trou dans une série
# temporelle se lit comme une rupture alors qu'il ne dit que « rien ce jour-là ».
FENETRE_JOURS = 30

# En deçà, il n'y a pas de tendance : un point unique ne trace rien et deux points
# tracent une droite qui n'informe pas. Le gabarit n'affiche alors aucune courbe.
MINIMUM_POINTS_COURBE = 2


def _grille(jours: int) -> list[date]:
    fin = date.today()
    return [fin - timedelta(days=n) for n in range(jours - 1, -1, -1)]


def _compter_par_jour(requete, colonne_date, depuis: datetime) -> dict[date, int]:
    """`{jour: nombre}` sur une colonne de date, quel que soit le modèle."""
    jour = func.date(colonne_date)
    return {
        (d if isinstance(d, date) else datetime.strptime(str(d), "%Y-%m-%d").date()): n
        for d, n in requete.filter(colonne_date >= depuis)
                           .with_entities(jour, func.count())
                           .group_by(jour).all()
    }


def _serie(db, requete, colonne_date, jours: int = FENETRE_JOURS) -> list[dict]:
    """Série journalière complète, zéros compris."""
    grille = _grille(jours)
    depuis = datetime.combine(grille[0], datetime.min.time())
    try:
        compte = _compter_par_jour(requete, colonne_date, depuis)
    except Exception:
        _log.exception("[graphiques] série indisponible")
        compte = {}
    return [{"jour": j.strftime("%d/%m"), "valeur": compte.get(j, 0)} for j in grille]


def _sources_de(db, id_entreprise: int | None):
    requete = db.query(SourceDonnee)
    if id_entreprise is not None:
        requete = requete.filter(SourceDonnee.idEntreprise == id_entreprise)
    return requete


def _imports_de(db, id_entreprise: int | None):
    requete = db.query(ImportDonnee).join(
        SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
    if id_entreprise is not None:
        requete = requete.filter(SourceDonnee.idEntreprise == id_entreprise)
    return requete


def _remonter(requete, id_entreprise: int | None):
    """Chaîne d'appartenance Résultat/Alerte → Configuration → Import → Source."""
    requete = (requete
               .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
               .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source))
    if id_entreprise is not None:
        requete = requete.filter(SourceDonnee.idEntreprise == id_entreprise)
    return requete


def _analyses_de(db, id_entreprise: int | None):
    return _remonter(
        db.query(ResultatAnalyse).join(
            ConfigurationAnalyse,
            ResultatAnalyse.id_configuration == ConfigurationAnalyse.id_configuration),
        id_entreprise)


def _alertes_de(db, id_entreprise: int | None):
    return _remonter(
        db.query(Alerte).join(
            ConfigurationAnalyse,
            Alerte.id_configuration == ConfigurationAnalyse.id_configuration),
        id_entreprise)


def series_entreprise(db, id_entreprise: int) -> dict[str, list[dict]]:
    """Les séries que le tableau de bord entreprise sait tracer."""
    return {
        "sources": _serie(db, _sources_de(db, id_entreprise), SourceDonnee.date_creation),
        "imports": _serie(db, _imports_de(db, id_entreprise), ImportDonnee.date_import),
        "analyses": _serie(db, _analyses_de(db, id_entreprise), ResultatAnalyse.date_execution),
        "alertes": _serie(db, _alertes_de(db, id_entreprise), Alerte.date_creation),
    }


def series_parc(db) -> dict[str, list[dict]]:
    """Les mêmes séries à l'échelle du parc, plus les inscriptions."""
    return {
        "inscriptions": _serie(db, db.query(Entreprise), Entreprise.date_inscription),
        "imports": _serie(db, _imports_de(db, None), ImportDonnee.date_import),
        "analyses": _serie(db, _analyses_de(db, None), ResultatAnalyse.date_execution),
        "alertes": _serie(db, _alertes_de(db, None), Alerte.date_creation),
        "sources": _serie(db, _sources_de(db, None), SourceDonnee.date_creation),
    }


def alertes_ouvertes_par_jour(db, id_entreprise: int | None = None,
                              jours: int = FENETRE_JOURS) -> list[dict]:
    """Encours d'alertes non traitées, jour par jour.

    ⚠️ C'est un **état**, pas un flux : « non traitée le jour J » vaut
    `créée ≤ J` et (`jamais traitée` ou `traitée après J`). Compter les créations
    donnerait une courbe qui ne redescend jamais, ce que l'encours fait pourtant.
    Reconstituable sans historique dédié parce que `Alerte.date_traitement` existe.
    """
    grille = _grille(jours)
    try:
        lignes = (_alertes_de(db, id_entreprise)
                  .with_entities(Alerte.date_creation, Alerte.date_traitement).all())
    except Exception:
        _log.exception("[graphiques] encours d'alertes indisponible")
        lignes = []

    serie = []
    for j in grille:
        borne = datetime.combine(j, datetime.max.time())
        n = sum(1 for creation, traitement in lignes
                if creation and creation <= borne
                and (traitement is None or traitement > borne))
        serie.append({"jour": j.strftime("%d/%m"), "valeur": n})
    return serie


def repartition_objectifs(db, id_entreprise: int, presenter) -> list[dict]:
    """Configurations par objectif, avec la couleur déjà définie pour chacun.

    `presenter` est `_presenter_objectif` : **source unique** de l'affichage d'un
    objectif. La passer en argument évite d'importer les routes depuis un service.
    """
    try:
        configs = _remonter(db.query(ConfigurationAnalyse), id_entreprise).all()
    except Exception:
        _log.exception("[graphiques] répartition par objectif indisponible")
        return []

    groupes: dict[tuple, dict] = {}
    for c in configs:
        vue = presenter(c)
        # Toutes les configurations « besoin libre » se regroupent sous un seul
        # libellé : les distinguer par leur texte produirait autant de parts que de
        # configurations, ce qui ne se lit pas.
        cle = ("libre",) if vue["libre"] else (vue["label"],)
        entree = groupes.setdefault(cle, {
            "label": "Besoin exprimé librement" if vue["libre"] else vue["label"],
            "color": vue["color"], "nombre": 0,
        })
        entree["nombre"] += 1

    total = sum(g["nombre"] for g in groupes.values()) or 1
    parts = sorted(groupes.values(), key=lambda g: g["nombre"], reverse=True)
    for g in parts:
        g["pct"] = round(g["nombre"] * 100 / total)
    return parts


def entreprises_actives(db, volumes: dict, limite: int = 5) -> list[dict]:
    """Les entreprises les plus actives, d'après des volumes déjà calculés.

    Aucun décompte n'est refait ici : `volumes` vient de
    `supervision.activite_par_entreprise`, producteur unique de ces chiffres.
    """
    try:
        noms = {e.idEntreprise: e.nom for e in db.query(Entreprise).all()}
    except Exception:
        _log.exception("[graphiques] noms d'entreprises indisponibles")
        return []

    lignes = []
    for ident, v in volumes.items():
        if ident == "_vide":
            continue
        lignes.append({
            "id": ident,
            "nom": noms.get(ident, f"Entreprise {ident}"),
            "sources": v.get("sources", 0),
            "analyses": v.get("analyses", 0),
            "alertes": v.get("alertes", 0),
            "erreurs": v.get("sources_erreur", 0),
            # Le classement porte sur l'usage réel — les analyses lancées — et non
            # sur le nombre de sources, qu'on déclare une fois et qui ne bouge plus.
            "rang": v.get("analyses", 0),
        })
    lignes.sort(key=lambda l: (l["rang"], l["sources"]), reverse=True)
    maximum = max((l["rang"] for l in lignes), default=0) or 1
    for l in lignes:
        l["part"] = round(l["rang"] * 100 / maximum)
    return lignes[:limite]
