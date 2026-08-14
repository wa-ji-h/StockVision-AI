"""
Genere les alertes des resultats DEJA en base (Module 5, reprise de l'existant).

Rien n'est invente : la criticite a ete calculee par le Module 4 et le message est le
`criticite_motif` deja stocke. Ce script ne fait que projeter en alertes les resultats de
rang >= 2 qui n'en ont pas encore.

Idempotent, et pas seulement « sans doublon » : il applique exactement la meme fonction
que le lancement (`enregistrer_alerte`), donc il cree, met a jour ET retire selon le rang
courant. Relancer apres avoir change les seuils de criticite realigne toutes les alertes.

Usage :
    python generer_alertes_existantes.py            # apercu, n'ecrit rien
    python generer_alertes_existantes.py --commit
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.database.connection import SessionLocal, engine

engine.echo = False
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

from app.database.models.alerte import Alerte
from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.resultat_analyse import ResultatAnalyse
from app.services.alertes import SEUIL_ALERTE, enregistrer_alerte, merite_alerte

TRAIT = "-" * 92


def main() -> None:
    commit = "--commit" in sys.argv
    db = SessionLocal()
    try:
        lignes = (db.query(ResultatAnalyse)
                    .order_by(ResultatAnalyse.date_execution, ResultatAnalyse.id_resultat)
                    .all())
        deja = {a.id_resultat for a in db.query(Alerte).all() if a.id_resultat}

        print(TRAIT)
        print(f"{len(lignes)} resultat(s) en base — seuil d'alerte : rang >= {SEUIL_ALERTE}")
        print(TRAIT)
        print(f"{'res':>4} {'cfg':>4} {'type':<11} {'rang':>4} {'niveau':<10} action")

        crees = majs = ignores = 0
        for ligne in lignes:
            cfg = db.get(ConfigurationAnalyse, ligne.id_configuration)
            rang = ligne.criticite_rang
            if not merite_alerte(rang):
                ignores += 1
                continue
            action = "MAJ" if ligne.id_resultat in deja else "CREATION"
            print(f"{ligne.id_resultat:>4} {ligne.id_configuration:>4} "
                  f"{(ligne.type_analyse or '?'):<11} {rang:>4} {(ligne.criticite or '?'):<10} "
                  f"{action} — {(ligne.criticite_motif or '')[:60]}")
            if commit and cfg:
                enregistrer_alerte(db, ligne, cfg)
            if action == "CREATION":
                crees += 1
            else:
                majs += 1

        print(f"\n{crees} a creer, {majs} a mettre a jour, {ignores} sous le seuil"
              f"{'' if commit else '  — APERCU, rien ecrit (ajouter --commit)'}")
        if commit:
            print(f"total d'alertes en base : {db.query(Alerte).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
