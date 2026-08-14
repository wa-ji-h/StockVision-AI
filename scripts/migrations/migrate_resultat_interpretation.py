"""
Migration : Module 4 - tache 4.5, restitution en langage naturel.

ResultatAnalyse : interpretation, interpretation_json, interpretation_source,
interpretation_erreur.

Ces colonnes sont toutes NULLABLE, et c'est le point de conception : quand le service
externe est indisponible, les colonnes numeriques ecrites par 4.4 restent renseignees
et consultables — seule l'interpretation redigee manque, avec son motif dans
interpretation_erreur. Aucune ligne existante n'a besoin d'etre retro-remplie.

Script idempotent : relancable sans effet de bord.

Usage : python migrate_resultat_interpretation.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)

AJOUTS = {
    "ResultatAnalyse": [
        ("interpretation", "ALTER TABLE ResultatAnalyse ADD COLUMN interpretation TEXT NULL"),
        ("interpretation_json", "ALTER TABLE ResultatAnalyse ADD COLUMN interpretation_json TEXT NULL"),
        ("interpretation_source", "ALTER TABLE ResultatAnalyse ADD COLUMN interpretation_source VARCHAR(20) NULL"),
        ("interpretation_erreur", "ALTER TABLE ResultatAnalyse ADD COLUMN interpretation_erreur TEXT NULL"),
    ],
}

with engine.begin() as conn:
    for table, colonnes in AJOUTS.items():
        existantes = [c["name"] for c in inspector.get_columns(table)]
        for nom, ddl in colonnes:
            if nom not in existantes:
                conn.execute(text(ddl))
                print(f"OK  {table}.{nom} ajoutee.")
            else:
                print(f"--  {table}.{nom} deja presente, rien a faire.")

print("\nMigration terminee.")
