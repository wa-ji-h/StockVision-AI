"""Tableau de bord entreprise — où elle en est dans sa chaîne de données.

L'entreprise ne supervise pas un parc : elle avance dans un pipeline. Ce module
décrit l'état réel de chaque maillon de la chaîne du modèle de données —
`SourceDonnee` → `ImportDonnee` → `ConfigurationAnalyse` → `ResultatAnalyse` →
`Alerte` — et en déduit **la prochaine action utile**.

⚠️ **Aucun décompte n'est recalculé ici.** Les volumes viennent de
`supervision.activite_par_entreprise`, producteur unique côté entreprise comme côté
administrateur. Ce module n'ajoute que ce que la supervision n'a pas à connaître :
l'ordre des étapes et le maillon qui bloque.

Ne lève jamais : une page d'accueil doit s'afficher même si un décompte échoue.
"""

from __future__ import annotations

import logging

from sqlalchemy import func

from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.import_donnee import ImportDonnee
from app.database.models.source_donnee import SourceDonnee

_log = logging.getLogger("stockvision.tableau_bord")

# L'ordre est celui de la chaîne, pas un choix d'affichage : chaque maillon exige le
# précédent. C'est ce qui rend le fil lisible comme une progression.
ETAPES = (
    ("sources", "Sources", "Vos bases et fichiers déclarés",
     "database", "/dashboard/entreprise/import"),
    ("imports", "Imports", "Données effectivement chargées",
     "cloud-arrow-up", "/dashboard/entreprise/imports"),
    ("configurations", "Analyses", "Ce que vous cherchez à savoir",
     "sliders2", "/dashboard/entreprise/configurations"),
    ("resultats", "Résultats", "Chiffres et interprétations produits",
     "graph-up", "/dashboard/entreprise/resultats"),
    ("alertes", "Alertes", "Ce qui mérite votre attention",
     "bell", "/dashboard/entreprise/alertes"),
)

# Que faire quand un maillon est vide — le premier maillon vide commande la page.
# Un tableau de bord qui constate sans dire quoi faire laisse dans l'impasse.
_PROCHAINE_ACTION = {
    "sources": ("Connectez vos premières données",
                "Déclarez une base de données ou déposez un fichier : c'est le point de "
                "départ de toute analyse.",
                "Ajouter une source", "/dashboard/entreprise/import"),
    "imports": ("Importez vos données",
                "Vos sources sont déclarées mais aucune donnée n'a encore été chargée.",
                "Lancer un import", "/dashboard/entreprise/import"),
    "configurations": ("Dites ce que vous cherchez à savoir",
                       "Vos données sont prêtes. Créez une analyse en choisissant un "
                       "objectif ou en formulant votre besoin.",
                       "Créer une analyse", "/dashboard/entreprise/configurations/new"),
    "resultats": ("Lancez votre première analyse",
                  "Vos analyses sont configurées mais aucune n'a encore été exécutée.",
                  "Voir mes analyses", "/dashboard/entreprise/configurations"),
}

# Tout est en place : on ne fabrique pas une action pour meubler.
_RIEN_A_FAIRE = ("Votre chaîne d'analyse est complète",
                 "Sources, analyses et résultats sont en place. Consultez vos résultats "
                 "ou ajoutez une nouvelle analyse.",
                 "Voir mes résultats", "/dashboard/entreprise/resultats")


def etat_pipeline(db, id_entreprise: int, volumes: dict) -> dict:
    """Fil du pipeline + prochaine action, à partir de volumes déjà calculés.

    `volumes` est la ligne produite par `activite_par_entreprise` : ce module ne
    requête que ce qu'elle ne porte pas (imports et configurations), en 2 requêtes.
    """
    compte = {
        "sources": volumes.get("sources", 0),
        "imports": 0,
        "configurations": 0,
        "resultats": volumes.get("analyses", 0),
        "alertes": volumes.get("alertes", 0),
    }
    try:
        compte["imports"] = (
            db.query(func.count(ImportDonnee.id_import))
            .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
            .filter(SourceDonnee.idEntreprise == id_entreprise).scalar() or 0
        )
        compte["configurations"] = (
            db.query(func.count(ConfigurationAnalyse.id_configuration))
            .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
            .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
            .filter(SourceDonnee.idEntreprise == id_entreprise).scalar() or 0
        )
    except Exception:
        _log.exception("[tableau_bord] volumes du pipeline indisponibles pour %s", id_entreprise)

    # Le premier maillon vide est le seul qui bloque : au-delà, tout est vide par
    # conséquence, pas par oubli. Le signaler partout serait une fausse alerte.
    bloquant = next((cle for cle, *_ in ETAPES if not compte[cle] and cle != "alertes"), None)

    etapes = []
    for rang, (cle, titre, sous_titre, icone, lien) in enumerate(ETAPES):
        etapes.append({
            "cle": cle, "titre": titre, "sous_titre": sous_titre,
            "icone": icone, "lien": lien, "nombre": compte[cle],
            # « fait » / « à faire » / « en attente du maillon précédent »
            "etat": ("fait" if compte[cle]
                     else "attendu" if cle == bloquant
                     else "en_attente"),
            "rang": rang + 1,
        })

    titre, texte, action, lien = _PROCHAINE_ACTION.get(bloquant, _RIEN_A_FAIRE)
    return {
        "etapes": etapes,
        "bloquant": bloquant,
        # `complet` distingue « rien à faire parce que tout est en place » de
        # « rien à faire parce qu'on ne sait pas quoi proposer ».
        "complet": bloquant is None,
        "prochaine": {"titre": titre, "texte": texte, "action": action, "lien": lien},
        "compte": compte,
    }
