"""
Jeu de donnees de DEMONSTRATION pour le Module 5 (alertes).

Cree deux analyses qui declenchent chacune un niveau different, sur deux types
d'analyse differents :

  * classement tres concentre  -> criticite « eleve »    (un produit capte 78 % du total)
  * anomalie marquee           -> criticite « critique »  (4 observations sur 30, soit 13 %)

Les valeurs ont ete CALIBREES contre criticite.py, pas devinees : le seuil de concentration
est a 70 % pour « eleve », celui du taux d'anomalies a 10 % pour « critique ». Le script
verifie le niveau obtenu et signale tout ecart.

⚠️ TOUT EST IDENTIFIABLE ET REVERSIBLE.
Sources, imports, configurations et fichiers portent le marqueur « DEMO-ALERTES ».
La suppression est integrale : `python demo_alertes.py --supprimer` retire les lignes
(la cascade emporte imports, configurations, resultats et alertes) et les fichiers CSV.
A executer avant la livraison finale.

Rien n'est insere a la main dans ResultatAnalyse ni dans Alerte : le script fait tourner la
VRAIE chaine 4.1 -> 4.2 -> 4.4 -> Module 5. Ce qui est montre est donc ce que produit
l'application, pas une mise en scene.

Usage :
    python demo_alertes.py              # apercu : ce qui serait cree, sans rien ecrire
    python demo_alertes.py --creer
    python demo_alertes.py --supprimer
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd

from app.database.connection import SessionLocal, engine

engine.echo = False
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.import_donnee import ImportDonnee
from app.database.models.resultat_analyse import ResultatAnalyse
from app.database.models.source_donnee import SourceDonnee
from app.services.moteur_analyse.extraction import UPLOAD_DIR

# Marqueur unique : il suffit pour retrouver et retirer tout le jeu de demonstration.
MARQUEUR = "DEMO-ALERTES"
TRAIT = "-" * 84

FICHIER_CLASSEMENT = f"{MARQUEUR}_ventes_par_produit.csv"
FICHIER_ANOMALIE = f"{MARQUEUR}_releves_stock.csv"


# ──────────────────────────────────────────────────────────────────────────────
# Donnees
# ──────────────────────────────────────────────────────────────────────────────

def _donnees_classement() -> pd.DataFrame:
    """Cinq produits dont un capte 78 % du total -> concentration au-dela du seuil « eleve ».

    Un catalogue ou un seul reference porte l'essentiel du chiffre est une situation
    metier reelle et risquee : c'est ce que l'alerte doit faire remonter.
    """
    produits = [
        ("Console Nova", 41200.0),
        ("Casque Aura", 5100.0),
        ("Clavier Lumen", 3300.0),
        ("Souris Orbit", 2100.0),
        ("Tapis Flux", 1050.0),
    ]
    debut = datetime(2026, 6, 1)
    lignes = []
    # Plusieurs lignes par produit : une agregation doit avoir de quoi agreger.
    for i, (produit, total) in enumerate(produits):
        for j in range(6):
            lignes.append({
                "date_vente": (debut + timedelta(days=i * 6 + j)).strftime("%Y-%m-%d"),
                "produit": produit,
                "montant": round(total / 6, 2),
                "region": ["Nord", "Sud", "Est"][j % 3],
            })
    return pd.DataFrame(lignes)


def _donnees_anomalie() -> pd.DataFrame:
    """Trente releves dont quatre tres au-dessus -> 13 % d'anomalies, au-dela du seuil
    « critique » (10 %).

    Graine fixe : la demonstration doit donner le meme resultat a chaque execution.
    """
    rng = np.random.default_rng(20260810)
    normales = list(np.round(rng.normal(120, 9, 26), 2))
    aberrantes = [384.0, 384.0, 384.0, 384.0]     # x3,2 — calibre contre criticite.py
    valeurs = normales[:8] + [aberrantes[0]] + normales[8:16] + aberrantes[1:3] \
        + normales[16:24] + [aberrantes[3]] + normales[24:]
    debut = datetime(2026, 6, 1)
    return pd.DataFrame({
        "date_releve": [(debut + timedelta(days=i)).strftime("%Y-%m-%d")
                        for i in range(len(valeurs))],
        "entrepot": [["Lyon", "Nantes", "Lille"][i % 3] for i in range(len(valeurs))],
        "quantite_stock": valeurs,
    })


JEUX = (
    {
        "fichier": FICHIER_CLASSEMENT,
        "source": f"{MARQUEUR} — Ventes par produit",
        "objectif": "comparaison_classement",
        "besoin": "Quels produits concentrent mon chiffre d'affaires ?",
        "donnees": _donnees_classement,
        "attendu": "eleve",
    },
    {
        "fichier": FICHIER_ANOMALIE,
        "source": f"{MARQUEUR} — Relevés de stock",
        "objectif": "detection_anomalie",
        "besoin": "Y a-t-il des relevés de stock anormaux ?",
        "donnees": _donnees_anomalie,
        "attendu": "critique",
    },
)


# ──────────────────────────────────────────────────────────────────────────────
# Creation
# ──────────────────────────────────────────────────────────────────────────────

def _entreprise(db) -> int:
    """L'entreprise proprietaire : celle qui possede deja des sources."""
    ligne = db.query(SourceDonnee.idEntreprise).first()
    if not ligne:
        raise SystemExit("Aucune entreprise avec des sources : creez-en une avant.")
    return ligne[0]


def creer(db, commit: bool) -> None:
    from app.services.moteur_analyse import (
        executer_calcul,
        executer_et_stocker,
        extraire_donnees,
        preparer_donnees,
        profiler_selection,
        specification_par_defaut,
    )

    id_entreprise = _entreprise(db)
    print(TRAIT)
    print(f"CREATION du jeu « {MARQUEUR} » pour l'entreprise {id_entreprise}")
    print(TRAIT)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    for jeu in JEUX:
        df = jeu["donnees"]()
        chemin = os.path.join(UPLOAD_DIR, jeu["fichier"])
        print(f"\n  {jeu['source']}")
        print(f"    fichier   : {jeu['fichier']} ({len(df)} lignes, "
              f"{len(df.columns)} colonnes)")
        if not commit:
            print(f"    objectif  : {jeu['objectif']}  (attendu : {jeu['attendu']})")
            continue

        df.to_csv(chemin, index=False, encoding="utf-8")

        source = SourceDonnee(type_source="CSV", nom=jeu["source"], statut="actif",
                              idEntreprise=id_entreprise)
        db.add(source)
        db.commit()

        imp = ImportDonnee(
            nom_fichier=jeu["fichier"], type_format="CSV", statut="valide",
            id_source=source.id_source, taille_octets=os.path.getsize(chemin),
            meta_json=json.dumps({"columns": list(df.columns)}, ensure_ascii=False),
            chemin_fichier=jeu["fichier"],
        )
        db.add(imp)
        db.commit()

        # Pour une source CSV, les cles de `facteurs_selectionnes` sont des COLONNES.
        facteurs = {"source_id": source.id_source,
                    "tables": {c: "*" for c in df.columns}}
        config = ConfigurationAnalyse(
            objectif=jeu["objectif"], besoin=jeu["besoin"], frequence="ponctuelle",
            statut="actif", id_import=imp.id_import,
            facteurs_selectionnes=json.dumps(facteurs, ensure_ascii=False),
            date_modification=datetime.now(),
        )
        db.add(config)
        db.commit()

        # Specification par le repli deterministe : aucun appel LLM, donc aucun quota
        # consomme et un resultat identique a chaque execution.
        profil = profiler_selection(db, source, facteurs["tables"])
        intention = specification_par_defaut(jeu["objectif"], profil)
        config.specification_json = json.dumps(intention.model_dump(), ensure_ascii=False)
        config.intention_reformulee = intention.reformulation
        db.commit()

        # La VRAIE chaine : extraction -> preparation -> calcul -> stockage -> alerte.
        prepare = preparer_donnees(extraire_donnees(db, config), config)
        calcul = executer_et_stocker(db, config, intention, prepare)
        if calcul is None:
            print(f"    ECHEC    : {config.execution_erreur}")
            continue

        c = calcul.criticite
        conforme = c["niveau"] == jeu["attendu"]
        print(f"    source {source.id_source} · import {imp.id_import} · "
              f"config {config.id_configuration} · resultat {calcul.id_resultat}")
        print(f"    criticite : {c['niveau']} (rang {c['rang']}) "
              f"{'OK' if conforme else 'ECART — attendu ' + jeu['attendu']}")
        print(f"    motif     : {c['motif']}")

    if commit:
        from app.database.models.alerte import Alerte
        from app.services.alertes import compter_non_lues

        alertes = db.query(Alerte).order_by(Alerte.id_alerte.desc()).limit(2).all()
        print(f"\n  alertes creees : {len(alertes)}")
        for a in alertes:
            print(f"    [{a.niveau}] resultat {a.id_resultat} — {a.message}")
        print(f"  badge « non lues » : {compter_non_lues(db, id_entreprise)}")
    else:
        print("\n  APERCU — rien ecrit (ajouter --creer)")


# ──────────────────────────────────────────────────────────────────────────────
# Suppression
# ──────────────────────────────────────────────────────────────────────────────

def supprimer(db, commit: bool) -> None:
    sources = db.query(SourceDonnee).filter(SourceDonnee.nom.like(f"{MARQUEUR}%")).all()
    print(TRAIT)
    print(f"SUPPRESSION du jeu « {MARQUEUR} »")
    print(TRAIT)
    if not sources:
        print("  aucune source de demonstration en base.")
    for s in sources:
        imports = db.query(ImportDonnee).filter(ImportDonnee.id_source == s.id_source).all()
        configs = db.query(ConfigurationAnalyse).filter(
            ConfigurationAnalyse.id_import.in_([i.id_import for i in imports])).all() if imports else []
        resultats = db.query(ResultatAnalyse).filter(
            ResultatAnalyse.id_configuration.in_([c.id_configuration for c in configs])
        ).all() if configs else []
        print(f"  source {s.id_source} « {s.nom} » : {len(imports)} import(s), "
              f"{len(configs)} configuration(s), {len(resultats)} resultat(s)")
        if commit:
            # La cascade emporte imports, configurations, resultats et alertes.
            db.delete(s)
    if commit:
        db.commit()

    for nom in (FICHIER_CLASSEMENT, FICHIER_ANOMALIE):
        chemin = os.path.join(UPLOAD_DIR, nom)
        if os.path.isfile(chemin):
            print(f"  fichier {nom}")
            if commit:
                os.remove(chemin)
    print("\n  supprime." if commit else "\n  APERCU — rien supprime (ajouter --supprimer)")


def main() -> None:
    db = SessionLocal()
    try:
        if "--supprimer" in sys.argv:
            supprimer(db, True)
        elif "--creer" in sys.argv:
            creer(db, True)
        else:
            creer(db, False)
            supprimer(db, False)
    finally:
        db.close()


if __name__ == "__main__":
    main()
