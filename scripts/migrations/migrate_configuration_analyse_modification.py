"""
Migration : Module 4 - page de resultats, horodatage des modifications.

ConfigurationAnalyse : date_modification.

Pourquoi : la page de resultats compare deux executions d'une meme configuration et
affiche l'evolution. Sans cette date, un changement de parametrage (colonnes, objectif,
besoin) entre deux executions serait presente comme une evolution metier. Renseignee a
chaque passage par /finalize, elle permet de supprimer le delta quand la configuration a
change entre-temps — plutot que d'afficher un chiffre faux.

Les lignes existantes restent a NULL : on ne sait pas si elles ont ete modifiees, et le
supposer serait aussi faux dans un sens que dans l'autre. Une configuration sans date de
modification est traitee comme non modifiee depuis sa creation.

Script idempotent : relancable sans effet de bord.

Usage : python migrate_configuration_analyse_modification.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)

AJOUTS = {
    "ConfigurationAnalyse": [
        ("date_modification",
         "ALTER TABLE ConfigurationAnalyse ADD COLUMN date_modification DATETIME NULL"),
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
