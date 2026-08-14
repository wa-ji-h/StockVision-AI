"""
Test du declenchement des alertes (Module 5).

Tourne sur une base SQLite EN MEMOIRE construite depuis les vrais modeles : la logique et
les contraintes testees sont exactement celles de la production, sans toucher a MySQL.

Verifie le contrat complet :
  - rang < 2 ne cree rien ; rang >= 2 cree une alerte ;
  - un resultat produit AU PLUS une alerte (relance sans doublon) ;
  - un changement de rang met l'alerte a jour ;
  - un retour sous le seuil retire l'alerte ;
  - le message est repris de criticite_motif sans reecriture ;
  - une configuration peut porter plusieurs alertes (l'ancienne unicite l'interdisait) ;
  - le decompte des non lues ignore les traitees ;
  - la vue admin n'expose aucun message.

Usage : python test_alertes.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.connection import Base
from app.database.models.alerte import Alerte
from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.import_donnee import ImportDonnee
from app.database.models.resultat_analyse import ResultatAnalyse
from app.database.models.source_donnee import SourceDonnee
from app.services.alertes import (
    SEUIL_ALERTE,
    STATUT_TRAITEE,
    compter_non_lues,
    compter_par_niveau,
    enregistrer_alerte,
    lister_alertes,
    marquer_traitee,
    merite_alerte,
    synthese_admin,
)

TRAIT = "-" * 76
echec = 0


def controle(libelle, obtenu, attendu):
    global echec
    ok = obtenu == attendu
    echec += not ok
    print(f"  {'OK  ' if ok else 'ECHEC'} {libelle:<52} {str(obtenu):<8} (attendu {attendu})")


ID_ENTREPRISE = 7
moteur = create_engine("sqlite:///:memory:")
Base.metadata.create_all(moteur)
db = sessionmaker(bind=moteur)()

# Chaine de propriete : source -> import -> configuration -> resultat.
db.add(SourceDonnee(id_source=1, nom="ventes", type_source="CSV", idEntreprise=ID_ENTREPRISE))
db.add(ImportDonnee(id_import=1, id_source=1, nom_fichier="ventes.csv"))
db.add(ConfigurationAnalyse(id_configuration=1, id_import=1, objectif="prevision_evolution",
                            frequence="quotidienne", facteurs_selectionnes="{}"))
db.commit()
cfg = db.get(ConfigurationAnalyse, 1)


def resultat(id_res, rang, niveau, motif, type_analyse="prevision"):
    r = ResultatAnalyse(id_resultat=id_res, id_configuration=1, type_analyse=type_analyse,
                        valeur_analyse="1", criticite=niveau, criticite_rang=rang,
                        criticite_motif=motif, date_execution=datetime.now())
    db.add(r)
    db.commit()
    return r


print(TRAIT)
print(f"SEUIL DE DECLENCHEMENT — rang >= {SEUIL_ALERTE}")
print(TRAIT)
for rang, attendu in ((0, False), (1, False), (2, True), (3, True)):
    controle(f"rang {rang} merite une alerte", merite_alerte(rang), attendu)
controle("rang absent (None)", merite_alerte(None), False)

print("\n" + TRAIT)
print("CREATION")
print(TRAIT)
r1 = resultat(1, 1, "attention", "Concentration de 52 %.")
enregistrer_alerte(db, r1, cfg)
controle("rang 1 ne cree rien", db.query(Alerte).count(), 0)

r2 = resultat(2, 2, "eleve", "Baisse prévue de 34 %.")
enregistrer_alerte(db, r2, cfg)
controle("rang 2 cree une alerte", db.query(Alerte).count(), 1)
a = db.query(Alerte).first()
controle("message repris sans reecriture", a.message, "Baisse prévue de 34 %.")
controle("niveau repris", a.niveau, "eleve")
controle("rattachee au resultat", a.id_resultat, 2)
controle("statut initial", a.statut, "nouvelle")
controle("type d'analyse conserve", a.type_alerte, "prevision")

print("\n" + TRAIT)
print("IDEMPOTENCE ET MISE A JOUR")
print(TRAIT)
enregistrer_alerte(db, r2, cfg)
enregistrer_alerte(db, r2, cfg)
controle("relancer ne cree pas de doublon", db.query(Alerte).count(), 1)

r2.criticite_rang, r2.criticite = 3, "critique"
r2.criticite_motif = "Baisse prévue de 70 %."
db.commit()
enregistrer_alerte(db, r2, cfg)
a = db.query(Alerte).filter(Alerte.id_resultat == 2).first()
controle("toujours une seule alerte", db.query(Alerte).count(), 1)
controle("niveau mis a jour", a.niveau, "critique")
controle("message mis a jour", a.message, "Baisse prévue de 70 %.")

r2.criticite_rang, r2.criticite = 1, "attention"
db.commit()
enregistrer_alerte(db, r2, cfg)
controle("retour sous le seuil : alerte retiree", db.query(Alerte).count(), 0)

print("\n" + TRAIT)
print("PLUSIEURS ALERTES POUR UNE MEME CONFIGURATION")
print(TRAIT)
print("  (l'ancien unique=True sur id_configuration l'interdisait)")
for i, (rang, niveau) in enumerate(((2, "eleve"), (3, "critique"), (2, "eleve")), start=10):
    enregistrer_alerte(db, resultat(i, rang, niveau, f"Motif {i}."), cfg)
controle("3 executions critiques -> 3 alertes", db.query(Alerte).count(), 3)
controle("toutes sur la meme configuration",
         len({a.id_configuration for a in db.query(Alerte).all()}), 1)

print("\n" + TRAIT)
print("CONSULTATION ET TRAITEMENT")
print(TRAIT)
controle("non lues", compter_non_lues(db, ID_ENTREPRISE), 3)
controle("comptes par niveau", compter_par_niveau(db, ID_ENTREPRISE),
         {"eleve": 2, "critique": 1})
controle("liste complete", len(lister_alertes(db, ID_ENTREPRISE)), 3)
controle("filtre critique", len(lister_alertes(db, ID_ENTREPRISE, "critique")), 1)

premiere = lister_alertes(db, ID_ENTREPRISE)[0]
controle("marquer traitee", marquer_traitee(db, premiere.id_alerte, ID_ENTREPRISE), True)
controle("sort du decompte", compter_non_lues(db, ID_ENTREPRISE), 2)
controle("reste consultable", len(lister_alertes(db, ID_ENTREPRISE)), 3)
controle("statut enregistre",
         db.get(Alerte, premiere.id_alerte).statut, STATUT_TRAITEE)
controle("remise en attente",
         marquer_traitee(db, premiere.id_alerte, ID_ENTREPRISE, False), True)
controle("revient au decompte", compter_non_lues(db, ID_ENTREPRISE), 3)

print("\n  cloisonnement :")
controle("une autre entreprise ne voit rien", compter_non_lues(db, 999), 0)
controle("et ne peut pas traiter", marquer_traitee(db, premiere.id_alerte, 999), False)

print("\n" + TRAIT)
print("VUE ADMIN — volumes seulement")
print(TRAIT)
synthese = synthese_admin(db)
controle("une ligne par entreprise", len(synthese), 1)
ligne = synthese[0]
controle("total", ligne["total"], 3)
controle("non traitees", ligne["non_traitees"], 3)
controle("repartition eleve", ligne["eleve"], 2)
controle("repartition critique", ligne["critique"], 1)
# L'administrateur supervise : aucun message metier ne doit transiter.
interdits = {"message", "motif", "criticite_motif", "id_resultat"}
controle("aucun champ de detail metier expose",
         sorted(interdits & set(ligne)), [])
print(f"       champs exposes : {sorted(ligne)}")

db.close()
print("\n" + TRAIT)
print("TOUT PASSE" if not echec else f"{echec} ECHEC(S)")
sys.exit(1 if echec else 0)
