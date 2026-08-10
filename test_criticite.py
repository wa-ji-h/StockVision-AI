"""
Test des regles de criticite (Module 4, tache 4.6).

Verifie la logique metier validee avant implementation :
  - un resultat non concluant est TOUJOURS normal, quelle que soit l'ampleur ;
  - la fiabilite plafonne, elle ne fait jamais monter ;
  - un classement ne va jamais jusqu'a « critique » ;
  - une prevision a intervalle plus large que la valeur plafonne a « attention » ;
  - chaque niveau porte une justification exploitable par le Module 5.

Aucun appel reseau, aucune base : les regles sont deterministes, le test aussi.

Usage :
    python test_criticite.py            # table des seuils + scenarios
    python test_criticite.py --matrice  # balayage complet variation x fiabilite
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.moteur_analyse.criticite import (
    LIBELLE_PAR_NIVEAU,
    NIVEAUX_CRITICITE,
    SEUILS_CONCENTRATION,
    SEUILS_ECART_ANOMALIE,
    SEUILS_TAUX_ANOMALIE,
    SEUILS_VARIATION,
    evaluer_criticite,
)

TRAIT = "-" * 78
echec = 0


def controle(libelle, obtenu, attendu):
    global echec
    ok = obtenu == attendu
    echec += not ok
    print(f"  {'OK  ' if ok else 'ECHEC'} {libelle:<52} -> {str(obtenu):<10} (attendu {attendu})")


def bonne(**ind):
    return {"conclusif": True, **ind}


def afficher_seuils():
    print(TRAIT)
    print("SEUILS — definition unique dans criticite.py")
    print(TRAIT)
    print("  niveaux      :", ", ".join(f"{c}({r})" for c, r, _ in NIVEAUX_CRITICITE))
    print(f"  variation %  : {SEUILS_VARIATION}")
    print(f"  taux anomalie: {SEUILS_TAUX_ANOMALIE}")
    print(f"  ecart/seuil  : {SEUILS_ECART_ANOMALIE}")
    print(f"  concentration: {SEUILS_CONCENTRATION}")


def test_non_concluant():
    print("\n" + TRAIT)
    print("REGLE ABSOLUE — l'incertitude n'est pas un signal")
    print(TRAIT)
    for type_analyse, ind in (
        ("prevision", {"conclusif": False, "variation_pct": 400.0, "sens": "indetermine"}),
        ("tendance", {"conclusif": False, "variation_pct": -90.0, "r2": 0.1}),
        ("anomalie", {"conclusif": False, "nb_anomalies": 0, "nb_observations": 50}),
        ("classement", {"conclusif": False, "concentration": 0.99}),
    ):
        c = evaluer_criticite(type_analyse, ind, {"code": "bonne"})
        controle(f"{type_analyse} non concluant (ampleur enorme)", c["niveau"], "normal")
    print(f"       motif : {c['motif']}")


def test_variation():
    print("\n" + TRAIT)
    print("AMPLEUR — prevision et tendance")
    print(TRAIT)
    cas = [(3.0, "normal"), (10.0, "attention"), (24.9, "attention"),
           (25.0, "eleve"), (49.9, "eleve"), (50.0, "critique"), (-70.0, "critique")]
    for variation, attendu in cas:
        c = evaluer_criticite("prevision",
                              bonne(variation_pct=variation, sens="baisse" if variation < 0 else "hausse",
                                    largeur_relative=0.2),
                              {"code": "bonne"})
        controle(f"prevision variation {variation:+.1f} %", c["niveau"], attendu)
    c = evaluer_criticite("tendance", bonne(variation_pct=-30.0, sens="baisse", r2=0.9),
                          {"code": "bonne"})
    controle("tendance -30 % (r2 0.9)", c["niveau"], "eleve")
    print(f"       motif : {c['motif']}")


def test_plafonds():
    print("\n" + TRAIT)
    print("PLAFONDS — jamais de montee, seulement des rabattements")
    print(TRAIT)
    fort = dict(variation_pct=80.0, sens="baisse", largeur_relative=0.2)
    for code, attendu in (("bonne", "critique"), ("limitee", "eleve"), ("indicative", "attention")):
        c = evaluer_criticite("prevision", bonne(**fort), {"code": code})
        controle(f"variation 80 % avec fiabilite {code}", c["niveau"], attendu)
        if c["plafonnements"]:
            print(f"       {c['plafonnements'][0]}")

    faible = dict(variation_pct=2.0, sens="hausse", largeur_relative=0.1)
    c = evaluer_criticite("prevision", bonne(**faible), {"code": "bonne"})
    controle("fiabilite bonne ne fait pas monter une variation de 2 %", c["niveau"], "normal")

    c = evaluer_criticite("prevision",
                          bonne(variation_pct=80.0, sens="hausse", largeur_relative=1.4),
                          {"code": "bonne"})
    controle("intervalle plus large que la valeur", c["niveau"], "attention")
    print(f"       {c['plafonnements'][0]}")

    c = evaluer_criticite("classement", bonne(concentration=0.95, premier="Produit C"),
                          {"code": "bonne"})
    controle("classement tres concentre (95 %)", c["niveau"], "eleve")
    # Les seuils de concentration s'arretent d'eux-memes a « eleve » : le plafond de type
    # est une garantie, pas un correctif. Il ne se declenche donc pas ici, et c'est voulu —
    # un plafonnement n'est signale que lorsqu'il rabat effectivement un niveau.
    controle("aucun plafonnement signale (rien a rabattre)", c["plafonnements"], [])
    print(f"       motif : {c['motif']}")


def test_anomalie():
    print("\n" + TRAIT)
    print("ANOMALIE — deux axes, on retient le plus eleve")
    print(TRAIT)
    c = evaluer_criticite("anomalie",
                          bonne(nb_anomalies=1, nb_observations=200, seuil_ecarts_types=2.5,
                                ecart_max=4.5),
                          {"code": "bonne"})
    controle("1 anomalie sur 200 mais ecart 4.5 (x1.8 du seuil)", c["niveau"], "critique")
    print(f"       motif : {c['motif']}")

    c = evaluer_criticite("anomalie",
                          bonne(nb_anomalies=25, nb_observations=200, seuil_ecarts_types=2.5,
                                ecart_max=2.6),
                          {"code": "bonne"})
    controle("25 anomalies sur 200 (12.5 %), ecarts faibles", c["niveau"], "critique")
    print(f"       motif : {c['motif']}")

    c = evaluer_criticite("anomalie",
                          bonne(nb_anomalies=2, nb_observations=200, seuil_ecarts_types=2.5,
                                ecart_max=2.6),
                          {"code": "bonne"})
    controle("2 anomalies sur 200 (1 %), ecart juste au seuil", c["niveau"], "attention")


def test_justification():
    print("\n" + TRAIT)
    print("JUSTIFICATION — une alerte doit pouvoir s'expliquer")
    print(TRAIT)
    c = evaluer_criticite("prevision",
                          bonne(variation_pct=-34.2, sens="baisse", largeur_relative=0.3),
                          {"code": "limitee"})
    for cle in ("niveau", "rang", "libelle", "motif", "indicateur", "valeur",
                "seuil_franchi", "plafonnements"):
        controle(f"champ « {cle} » present", cle in c, True)
    print(f"\n  niveau        : {c['niveau']} (rang {c['rang']}, « {c['libelle']} »)")
    print(f"  motif         : {c['motif']}")
    print(f"  declencheur   : {c['indicateur']} = {c['valeur']} (seuil {c['seuil_franchi']})")
    print(f"  plafonnements : {c['plafonnements']}")


def matrice():
    print("\n" + TRAIT)
    print("MATRICE — variation x fiabilite (prevision, intervalle etroit)")
    print(TRAIT)
    fiabilites = ["indicative", "limitee", "bonne"]
    print(f"  {'variation':>10} | " + " | ".join(f"{f:^10}" for f in fiabilites))
    print("  " + "-" * 48)
    for v in (5, 12, 30, 60, 120):
        cellules = []
        for f in fiabilites:
            c = evaluer_criticite("prevision",
                                  bonne(variation_pct=float(v), sens="hausse", largeur_relative=0.2),
                                  {"code": f})
            cellules.append(f"{c['niveau']:^10}")
        print(f"  {v:>9} % | " + " | ".join(cellules))


def main():
    afficher_seuils()
    if "--matrice" in sys.argv:
        matrice()
        return
    test_non_concluant()
    test_variation()
    test_plafonds()
    test_anomalie()
    test_justification()
    matrice()
    print("\n" + TRAIT)
    print("TOUT PASSE" if not echec else f"{echec} ECHEC(S)")
    sys.exit(1 if echec else 0)


if __name__ == "__main__":
    main()
