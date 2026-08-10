"""
Migration : Module 4 - moteur d'execution.

1. ResultatAnalyse : modele_applique, fiabilite, intervalle_bas/haut, resultat_json.
   Le modele applique est conserve pour que l'entreprise sache sur quelle methode repose
   son analyse (ARIMA et regression lineaire ne se valent pas).
2. ConfigurationAnalyse : execution_erreur, colonne DEDIEE — chaque etape ecrit dans la
   sienne, sinon la derniere efface la trace des precedentes.

Usage : python migrate_execution_resultat.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)

AJOUTS = {
    "ResultatAnalyse": [
        ("modele_applique", "ALTER TABLE ResultatAnalyse ADD COLUMN modele_applique VARCHAR(50) NULL"),
        ("fiabilite", "ALTER TABLE ResultatAnalyse ADD COLUMN fiabilite VARCHAR(20) NULL"),
        ("intervalle_bas", "ALTER TABLE ResultatAnalyse ADD COLUMN intervalle_bas TEXT NULL"),
        ("intervalle_haut", "ALTER TABLE ResultatAnalyse ADD COLUMN intervalle_haut TEXT NULL"),
        ("resultat_json", "ALTER TABLE ResultatAnalyse ADD COLUMN resultat_json TEXT NULL"),
    ],
    "ConfigurationAnalyse": [
        ("execution_erreur", "ALTER TABLE ConfigurationAnalyse ADD COLUMN execution_erreur TEXT NULL"),
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
                print(f"--  {table}.{nom} existe deja.")

print("Migration terminee.")
