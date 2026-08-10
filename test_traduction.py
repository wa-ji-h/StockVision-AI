"""
Test manuel de la couche de traduction (Module 4).

Traduit plusieurs formulations en langage naturel et affiche le JSON produit.
AUCUN calcul n'est execute : on s'arrete a la specification validee.

Usage :
    python test_traduction.py                  # sources disponibles + etat du LLM
    python test_traduction.py <id_source>      # joue toutes les formulations d'exemple
    python test_traduction.py <id_source> "quels sont mes produits les plus vendus"
    python test_traduction.py <id_source> --repli    # force le repli deterministe

Sans LLM_API_KEY dans .env, le repli deterministe s'applique automatiquement :
le systeme reste entierement demontrable sans acces au LLM.
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import SessionLocal, engine

engine.echo = False
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.database.models.import_donnee import ImportDonnee
from app.database.models.source_donnee import SourceDonnee
from app.services.moteur_analyse import profiler_selection
from app.services.moteur_analyse.schema_analyse import SpecificationInvalide
from app.services.moteur_analyse.traduction import llm_disponible, traduire_intention

# Formulations reelles attendues, couvrant les 4 operations du vocabulaire.
EXEMPLES = [
    ("quels sont mes produits les plus vendus", "comparaison_classement"),
    ("quels articles devrais-je mettre en promotion", "comparaison_classement"),
    ("quels produits generent le plus de commandes", "comparaison_classement"),
    ("comment vont evoluer mes ventes le mois prochain", "prevision_evolution"),
    ("est-ce que mes ventes montent ou baissent", "prevision_tendance"),
    ("y a-t-il des valeurs anormales dans mes donnees", "detection_anomalie"),
    ("", "prevision_evolution"),  # objectif seul, sans besoin exprime
]


def lister(db):
    print("\nSources disponibles :")
    for s in db.query(SourceDonnee).all():
        print(f"  src{s.id_source}  {s.type_source:<4} {s.nom}")
    print()
    etat = "CONFIGUREE" if llm_disponible() else "ABSENTE -> repli deterministe"
    print(f"LLM_API_KEY : {etat}")
    print(f"LLM_MODEL   : {settings.LLM_MODEL}")
    print(f"LLM_BASE_URL: {settings.LLM_BASE_URL}")
    print("\nRelancez avec :  python test_traduction.py <id_source>")


def colonnes_de(db, source):
    """Selection = toutes les colonnes connues de la source."""
    imp = (
        db.query(ImportDonnee)
        .filter(ImportDonnee.id_source == source.id_source)
        .order_by(ImportDonnee.date_import.desc())
        .first()
    )
    meta = json.loads(imp.meta_json or "{}") if imp else {}
    if source.type_source == "CSV":
        return {c: "*" for c in meta.get("columns", [])}
    if source.type_source == "SQL":
        tables = meta.get("tables") or []
        return {t: "*" for t in tables} or {source.nom: "*"}
    connexion = meta.get("tables") or []
    return {t: "*" for t in connexion} if connexion else {}


def jouer(besoin, objectif, profil, forcer_repli):
    titre = besoin or "(aucun besoin exprime — objectif seul)"
    print("=" * 84)
    print(f"  « {titre} »")
    print(f"  objectif choisi : {objectif or '(aucun)'}")
    print("=" * 84)

    cle = settings.LLM_API_KEY
    if forcer_repli:
        settings.LLM_API_KEY = ""
    try:
        r = traduire_intention(besoin, objectif, profil)
    except SpecificationInvalide as e:
        print(f"  INTERRUPTION : {e.message_complet()}\n")
        return
    finally:
        settings.LLM_API_KEY = cle

    print(f"  source      : {r.source}" + (f"  (repli : {r.motif_repli})" if r.motif_repli else ""))
    print(f"  reformulation : {r.reformulation}")
    print("  specification produite :")
    print(json.dumps(r.intention.specification.model_dump(), indent=4, ensure_ascii=False))
    if r.intention.filtres:
        print("  filtres :", json.dumps([f.model_dump() for f in r.intention.filtres], ensure_ascii=False))
    print()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    forcer_repli = "--repli" in sys.argv

    db = SessionLocal()
    try:
        if not args:
            lister(db)
            return

        source = db.query(SourceDonnee).filter_by(id_source=int(args[0])).first()
        if not source:
            print(f"Source {args[0]} introuvable.")
            return

        selection = colonnes_de(db, source)
        if not selection:
            print(f"Aucune colonne connue pour la source {source.id_source}.")
            return

        profil = profiler_selection(db, source, selection)
        print(f"\nSource   : {source.nom} ({source.type_source})")
        print("Colonnes :")
        for table, infos in profil.items():
            for c in infos["colonnes"]:
                nature = "date" if c["est_date"] else ("numerique" if c["est_numerique"] else "texte")
                print(f"  - {c['nom']:<20} {nature}")
        mode = "REPLI FORCE" if forcer_repli else ("LLM" if llm_disponible() else "REPLI (pas de cle)")
        print(f"\nMode : {mode}\n")

        if len(args) > 1:
            jouer(args[1], args[2] if len(args) > 2 else None, profil, forcer_repli)
        else:
            for besoin, objectif in EXEMPLES:
                jouer(besoin, objectif, profil, forcer_repli)
    finally:
        db.close()


if __name__ == "__main__":
    main()
