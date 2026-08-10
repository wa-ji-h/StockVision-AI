"""
Test manuel du moteur d'execution (Module 4).

Lance la chaine complete 4.1 -> 4.2 -> traduction -> execution sur une configuration
reelle, et affiche les chiffres produits.

Usage :
    python test_execution.py                  # liste les configurations
    python test_execution.py <id_config>      # execute cette configuration
    python test_execution.py --matrice        # 4 operations x 3 fiabilites, sur donnees
                                              # de demo generees puis supprimees
    python test_execution.py <id> --commit    # ecrit le resultat en base

Le mode --matrice est le plus utile pour valider : il montre le meme calcul sous les
trois modeles (ARIMA / lissage exponentiel / regression lineaire) selon le volume.
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

import numpy as np
import pandas as pd

from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.import_donnee import ImportDonnee
from app.database.models.source_donnee import SourceDonnee
from app.services.moteur_analyse import (
    ExecutionError,
    SpecificationInvalide,
    executer_calcul,
    executer_et_stocker,
    extraire_donnees,
    preparer_donnees,
    profiler_selection,
    traduire_intention,
)
from app.services.moteur_analyse.execution import LIBELLE_MODELE
from app.services.moteur_analyse.fiabilite import evaluer_fiabilite
from app.services.moteur_analyse.preparation import TablePreparee
from app.services.moteur_analyse.schema_analyse import (
    IntentionAnalysee,
    SpecAnomalie,
    SpecClassement,
    SpecPrevision,
    SpecTendance,
)


def afficher(r):
    print(f"  type            : {r.type_analyse}")
    print(f"  modele applique : {r.modele_libelle}  ({r.modele_applique})")
    print(f"  fiabilite       : {r.fiabilite.get('label')} — {r.fiabilite.get('points')} points")
    print(f"  valeur analysee : {r.valeur_analyse}" + (f"  [{r.unite}]" if r.unite else ""))
    if r.valeur_prevue is not None:
        print(f"  valeur prevue   : {round(r.valeur_prevue, 3)}")
    if r.intervalle_bas is not None:
        print(f"  intervalle 95%  : [{round(r.intervalle_bas, 3)} ; {round(r.intervalle_haut, 3)}]"
              f"  (methode : {r.methode_intervalle})")
    if r.indicateurs:
        concl = r.indicateurs.get("conclusif")
        marque = "OUI" if concl else "NON  <-- resultat non concluant"
        print(f"  CONCLUSIF       : {marque}")
        print(f"  indicateurs     : " + "  ".join(
            f"{k}={v}" for k, v in r.indicateurs.items() if k != "conclusif"))
    if r.elements_classes:
        print(f"  classement ({len(r.elements_classes)} elements) :")
        for e in r.elements_classes[:5]:
            print(f"      {e['rang']}. {e['element']:<24} {round(e['valeur'], 2)}")
    if r.observations_aberrantes:
        print(f"  anomalies ({len(r.observations_aberrantes)}) :")
        for o in r.observations_aberrantes[:5]:
            print(f"      {o['date'][:10]}  {round(o['valeur'], 2):>10}  "
                  f"{o['ecart_zscore']:+} ecarts-types ({o['sens']})")
    if r.serie_historique:
        print(f"  serie historique: {len(r.serie_historique)} points")
    if r.serie_prevue:
        print(f"  serie prevue    : {len(r.serie_prevue)} points")
        for p in r.serie_prevue[:3]:
            extra = f"  [{round(p['bas'],2)} ; {round(p['haut'],2)}]" if "bas" in p else ""
            print(f"      {p['date'][:10]}  {round(p['valeur'], 2)}{extra}")
    for a in r.avertissements:
        print(f"  ! {a}")


def table_synthetique(n_points, graine=7):
    """Serie de ventes synthetique : tendance croissante + bruit + 2 anomalies."""
    rng = np.random.default_rng(graine)
    dates = pd.date_range("2026-01-01", periods=n_points, freq="D")
    base = 100 + np.arange(n_points) * 2.5 + rng.normal(0, 6, n_points)
    if n_points >= 8:                      # anomalies franches, pour etre detectables
        base[n_points // 3] += 90
        base[2 * n_points // 3] -= 85
    produits = ["Clavier", "Souris", "Ecran", "Casque"]
    df = pd.DataFrame(
        {
            "montant": np.round(base, 2),
            "quantite": rng.integers(1, 40, n_points),
            "produit": [produits[i % len(produits)] for i in range(n_points)],
        },
        index=dates,
    )
    df.index.name = "date"
    return TablePreparee(
        nom="ventes_demo", donnees=df, colonne_temps="date", pas="D", pas_label="jour",
        colonnes_valeurs=["montant", "quantite"], colonnes_categorielles=["produit"],
        n_points=n_points, n_periodes=n_points, n_lignes_source=n_points,
        fiabilite=evaluer_fiabilite(n_points),
    )


def matrice():
    """4 operations x 3 fiabilites — c'est le tableau a valider."""
    volumes = [(30, "bonne"), (14, "limitee"), (6, "indicative")]
    specs = [
        ("CLASSEMENT", SpecClassement(type_analyse="classement", dimension="produit",
                                      mesure="montant", agregation="somme",
                                      ordre="decroissant", limite=5)),
        ("PREVISION",  SpecPrevision(type_analyse="prevision", colonne_temps="date",
                                     mesure="montant", horizon=3)),
        ("TENDANCE",   SpecTendance(type_analyse="tendance", colonne_temps="date",
                                    mesure="montant")),
        ("ANOMALIE",   SpecAnomalie(type_analyse="anomalie", mesure="montant",
                                    colonne_temps="date", sensibilite="moyenne")),
    ]
    for n, attendu in volumes:
        table = table_synthetique(n)
        f = table.fiabilite
        print("\n" + "#" * 88)
        print(f"#  {n} POINTS  ->  fiabilite {f['code'].upper()}  (attendu : {attendu})"
              f"  ->  modele de prevision : {LIBELLE_MODELE[{'bonne':'arima','limitee':'lissage_exponentiel','indicative':'regression_lineaire'}[f['code']]]}")
        print("#" * 88)
        for libelle, spec in specs:
            intention = IntentionAnalysee(
                specification=spec,
                reformulation=f"Test automatique de l'operation {libelle.lower()}.",
            )
            print(f"\n--- {libelle} ---")
            try:
                afficher(executer_calcul(intention, table, config_id=0))
            except ExecutionError as e:
                print(f"  ECHEC : {e.message_complet()}")


def lister(db):
    print(f"\n{'id':>4}  {'statut':<10} {'objectif / besoin':<44} exec")
    print("-" * 80)
    for c in db.query(ConfigurationAnalyse).order_by(ConfigurationAnalyse.id_configuration).all():
        quoi = c.objectif or (c.besoin or "(vide)")[:42]
        print(f"{c.id_configuration:>4}  {c.statut or '-':<10} {quoi:<44} {c.statut_execution or '-'}")
    print("\n  python test_execution.py <id>        # chaine complete sur une config reelle")
    print("  python test_execution.py --matrice   # 4 operations x 3 fiabilites")


def executer_reelle(db, config_id, commit):
    config = db.query(ConfigurationAnalyse).filter_by(id_configuration=config_id).first()
    if not config:
        print(f"Configuration {config_id} introuvable.")
        return

    print(f"\nConfiguration {config_id} — objectif={config.objectif or '(vide)'} "
          f"besoin={(config.besoin or '(vide)')[:50]!r}")

    imp = db.query(ImportDonnee).filter_by(id_import=config.id_import).first()
    source = db.query(SourceDonnee).filter_by(id_source=imp.id_source).first()
    facteurs = json.loads(config.facteurs_selectionnes or "{}")
    selection = facteurs.get("tables", {})

    print("\n[1/4] extraction…")
    resultat = extraire_donnees(db, config)
    print(f"      {sum(len(d) for d in resultat.donnees.values())} lignes, "
          f"{len(resultat.donnees)} table(s)")

    print("[2/4] preparation…")
    prepare = preparer_donnees(resultat, config)
    t = prepare.table
    print(f"      {t.n_points} points — fiabilite {t.fiabilite['label']}")

    print("[3/4] traduction…")
    profil = profiler_selection(db, source, selection)
    trad = traduire_intention(config.besoin or "", config.objectif or None, profil)
    print(f"      source={trad.source} — {trad.reformulation}")

    print("[4/4] execution…\n")
    if commit:
        r = executer_et_stocker(db, config, trad.intention, prepare)
        if r is None:
            print(f"  ECHEC : {config.execution_erreur}")
            return
        print("  (resultat ecrit en base)")
    else:
        r = executer_calcul(trad.intention, t, config_id)
    afficher(r)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--matrice" in sys.argv:
        matrice()
        return

    db = SessionLocal()
    try:
        if not args:
            lister(db)
            return
        try:
            executer_reelle(db, int(args[0]), "--commit" in sys.argv)
        except (ExecutionError, SpecificationInvalide) as e:
            print(f"\n  ECHEC : {e.message_complet()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
