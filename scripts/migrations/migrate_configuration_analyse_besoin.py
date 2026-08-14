"""
Migration : Module 4 - couche de traduction de l'intention.

1. Renomme precision_complementaire -> besoin (le champ devient le besoin principal
   exprime en langage naturel, plus une simple precision optionnelle).
2. Ajoute specification_json et intention_reformulee (sortie de la traduction).

Idempotent : verifie l'existence de chaque colonne avant d'agir. Le renommage preserve
les donnees existantes (aucune ligne ne portait de valeur au moment de la migration,
mais le RENAME est sur de toute facon).

Usage : python migrate_configuration_analyse_besoin.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConfigurationAnalyse")]

with engine.begin() as conn:
    # 1. Renommage
    if "besoin" in columns:
        print("--  Colonne besoin existe deja.")
    elif "precision_complementaire" in columns:
        conn.execute(text(
            "ALTER TABLE ConfigurationAnalyse "
            "CHANGE COLUMN precision_complementaire besoin TEXT NULL"
        ))
        print("OK  precision_complementaire renommee en besoin (donnees preservees).")
    else:
        conn.execute(text("ALTER TABLE ConfigurationAnalyse ADD COLUMN besoin TEXT NULL"))
        print("OK  Colonne besoin creee (aucune colonne source a renommer).")

    # 2. Colonnes de traduction
    for col_name, ddl in [
        ("specification_json", "ALTER TABLE ConfigurationAnalyse ADD COLUMN specification_json TEXT NULL"),
        ("intention_reformulee", "ALTER TABLE ConfigurationAnalyse ADD COLUMN intention_reformulee TEXT NULL"),
    ]:
        if col_name not in columns:
            conn.execute(text(ddl))
            print(f"OK  Colonne {col_name} ajoutee.")
        else:
            print(f"--  Colonne {col_name} existe deja.")

print("Migration terminee.")
