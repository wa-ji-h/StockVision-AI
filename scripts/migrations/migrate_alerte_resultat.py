"""
Migration : Module 5 - alertes rattachees au RESULTAT.

⚠️ CHANGEMENT DE RELATION — a repercuter dans le diagramme de classes :
       avant : ConfigurationAnalyse 1 --- 1 Alerte   (unique=True)
       apres : ResultatAnalyse      1 --- 0..1 Alerte
               ConfigurationAnalyse 1 --- 0..n Alerte  (sans unicite)

Pourquoi : `unique=True` sur id_configuration interdisait plus d'une alerte par
configuration A VIE, alors qu'une analyse recurrente doit alerter a chaque execution
critique. Et rattachee a la configuration, l'alerte ne savait pas de quelle execution
elle parlait : impossible de renvoyer vers le bon resultat.

Colonnes ajoutees : id_resultat, criticite_rang, statut, date_traitement.
Contrainte retiree : l'unicite sur id_configuration.
Contrainte ajoutee : unicite sur id_resultat (un resultat = au plus une alerte).

Script idempotent : relancable sans effet de bord.

Usage : python migrate_alerte_resultat.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import inspect, text

from app.database.connection import engine

AJOUTS = [
    ("id_resultat", "ALTER TABLE Alerte ADD COLUMN id_resultat INT NULL"),
    ("criticite_rang", "ALTER TABLE Alerte ADD COLUMN criticite_rang INT NULL"),
    ("statut", "ALTER TABLE Alerte ADD COLUMN statut VARCHAR(20) DEFAULT 'nouvelle'"),
    ("date_traitement", "ALTER TABLE Alerte ADD COLUMN date_traitement DATETIME NULL"),
]

INDEX = [
    ("ix_alerte_statut_date", "CREATE INDEX ix_alerte_statut_date ON Alerte (statut, date_creation)"),
    ("ix_alerte_configuration", "CREATE INDEX ix_alerte_configuration ON Alerte (id_configuration)"),
    ("uq_alerte_resultat", "CREATE UNIQUE INDEX uq_alerte_resultat ON Alerte (id_resultat)"),
]

with engine.begin() as conn:
    inspector = inspect(engine)
    existantes = [c["name"] for c in inspector.get_columns("Alerte")]
    for nom, ddl in AJOUTS:
        if nom not in existantes:
            conn.execute(text(ddl))
            print(f"OK  Alerte.{nom} ajoutee.")
        else:
            print(f"--  Alerte.{nom} deja presente, rien a faire.")

    # Retrait de l'unicite sur id_configuration.
    #
    # MySQL refuse de supprimer un index dont depend une cle etrangere (erreur 1553) : il
    # faut retirer la contrainte AVANT l'index, puis la remettre. Elle se reappuiera alors
    # sur l'index simple cree juste apres, sans unicite.
    inspector = inspect(engine)
    unique_config = next(
        (i for i in inspector.get_indexes("Alerte")
         if i.get("unique") and i.get("column_names") == ["id_configuration"]),
        None,
    )
    if unique_config:
        fk_config = next(
            (fk for fk in inspector.get_foreign_keys("Alerte")
             if fk.get("constrained_columns") == ["id_configuration"]),
            None,
        )
        if fk_config and fk_config.get("name"):
            conn.execute(text(f"ALTER TABLE Alerte DROP FOREIGN KEY `{fk_config['name']}`"))
            print(f"--  cle etrangere {fk_config['name']} retiree temporairement.")

        conn.execute(text(f"DROP INDEX `{unique_config['name']}` ON Alerte"))
        print(f"OK  unicite sur id_configuration retiree (index {unique_config['name']}).")

        conn.execute(text(
            "CREATE INDEX ix_alerte_configuration ON Alerte (id_configuration)"))
        print("OK  index simple ix_alerte_configuration cree.")

        if fk_config:
            conn.execute(text(
                "ALTER TABLE Alerte ADD CONSTRAINT fk_alerte_configuration "
                "FOREIGN KEY (id_configuration) "
                "REFERENCES ConfigurationAnalyse(id_configuration) ON DELETE CASCADE"
            ))
            print("OK  cle etrangere id_configuration retablie (sans unicite).")
    else:
        print("--  aucune unicite sur id_configuration, rien a retirer.")

    # La cle etrangere doit exister avant l'index unique sur id_resultat.
    fks = [fk.get("constrained_columns") for fk in inspect(engine).get_foreign_keys("Alerte")]
    if ["id_resultat"] not in fks:
        conn.execute(text(
            "ALTER TABLE Alerte ADD CONSTRAINT fk_alerte_resultat "
            "FOREIGN KEY (id_resultat) REFERENCES ResultatAnalyse(id_resultat) ON DELETE CASCADE"
        ))
        print("OK  cle etrangere Alerte.id_resultat -> ResultatAnalyse ajoutee.")
    else:
        print("--  cle etrangere id_resultat deja presente.")

    noms_index = [i["name"] for i in inspect(engine).get_indexes("Alerte")]
    for nom, ddl in INDEX:
        if nom not in noms_index:
            conn.execute(text(ddl))
            print(f"OK  index {nom} cree.")
        else:
            print(f"--  index {nom} deja present.")

print("\nMigration terminee.")
