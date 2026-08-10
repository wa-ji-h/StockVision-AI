"""
Migration : Module 4 - tache 4.6, niveau de criticite.

ResultatAnalyse : criticite, criticite_rang, criticite_motif.

Colonnes DEDIEES plutot qu'un champ dans resultat_json : le Module 5 (alertes) doit
pouvoir filtrer en SQL — « tous les resultats de rang >= 2 depuis hier » — sans
deserialiser chaque ligne. criticite_motif conserve la justification, pour qu'une alerte
puisse expliquer son declenchement sans rejouer le calcul.

Les lignes anterieures restent a NULL : elles ont ete produites avant que la criticite
existe, et lui attribuer une valeur retroactivement inventerait un verdict. Relancer les
configurations concernees suffit a les renseigner.

Script idempotent : relancable sans effet de bord.

Usage : python migrate_resultat_criticite.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)

AJOUTS = {
    "ResultatAnalyse": [
        ("criticite", "ALTER TABLE ResultatAnalyse ADD COLUMN criticite VARCHAR(20) NULL"),
        ("criticite_rang", "ALTER TABLE ResultatAnalyse ADD COLUMN criticite_rang INT NULL"),
        ("criticite_motif", "ALTER TABLE ResultatAnalyse ADD COLUMN criticite_motif TEXT NULL"),
    ],
}

# Le Module 5 interrogera surtout « les resultats critiques recents » : un index sur le
# rang evite un balayage complet des que la table grossit.
INDEX = [
    ("ResultatAnalyse", "ix_resultat_criticite_rang",
     "CREATE INDEX ix_resultat_criticite_rang ON ResultatAnalyse (criticite_rang)"),
]

with engine.begin() as conn:
    for table, colonnes in AJOUTS.items():
        existantes = [c["name"] for c in inspector.get_columns(table)]
        for nom, ddl in colonnes:
            if nom not in existantes:
                conn.execute(text(ddl))
                print(f"OK  {table}.{nom} ajoutee.")
            else:
                print(f"--  {table}.{nom} deja presente, rien a faire.")

    for table, nom_index, ddl in INDEX:
        existants = [i["name"] for i in inspect(engine).get_indexes(table)]
        if nom_index not in existants:
            conn.execute(text(ddl))
            print(f"OK  index {nom_index} cree.")
        else:
            print(f"--  index {nom_index} deja present, rien a faire.")

print("\nMigration terminee.")
