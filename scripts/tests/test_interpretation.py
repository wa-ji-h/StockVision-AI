"""
Test manuel de la restitution en langage naturel (Module 4, tache 4.5).

Verifie les trois garanties de la couche, sans dependre d'une cle API pour les deux
premieres :

    python test_interpretation.py --frontiere   # ce qui est transmis, et ce qui ne l'est
                                                # jamais (aucun appel reseau)
    python test_interpretation.py --plafond     # le garde-fou de confiance
    python test_interpretation.py --indispo     # comportement sans cle API
    python test_interpretation.py <id_config>   # chaine complete sur une configuration
    python test_interpretation.py <id> --commit # ecrit l'interpretation en base

Sans cle API, --frontiere et --plafond passent quand meme : ils testent notre code,
pas le service externe.
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

from app.services.moteur_analyse.execution import ResultatExecution
from app.services.moteur_analyse.interpretation import (
    MAX_ELEMENTS_TRANSMIS,
    InterpretationRedigee,
    _plafonner_confiance,
    charge_utile,
    confiance_maximale,
    construire_prompt,
    interpreter_resultat,
    llm_disponible,
    schema_reponse,
)

TRAIT = "-" * 74


def _resultat_demo(**surcharges) -> ResultatExecution:
    """Un resultat de prevision plausible, surchargeable pour chaque scenario."""
    base = dict(
        config_id=999,
        type_analyse="prevision",
        valeur_analyse=25.0,
        valeur_prevue=29.56,
        intervalle_bas=17.09,
        intervalle_haut=42.03,
        methode_intervalle="modele",
        modele_applique="arima",
        fiabilite={"code": "bonne", "points": 30, "phrase": "Fiabilite bonne (30 points)."},
        unite="montant_total",
        indicateurs={
            "sens": "indetermine",
            "conclusif": False,
            "intervalle_contient_depart": True,
            "largeur_intervalle": 24.94,
            "largeur_relative": 0.84,
            "variation_pct": 18.24,
        },
        serie_historique=[
            {"date": "2026-01-01", "valeur": 21.0},
            {"date": "2026-02-01", "valeur": 24.5},
            {"date": "2026-03-01", "valeur": 25.0},
        ],
        avertissements=[],
    )
    base.update(surcharges)
    return ResultatExecution(**base)


# ---------------------------------------------------------------------------
# 1. La frontiere : ce qui est transmis, et ce qui ne l'est jamais
# ---------------------------------------------------------------------------

def test_frontiere() -> bool:
    print(TRAIT)
    print("FRONTIERE — aucune donnee brute ne doit franchir la limite")
    print(TRAIT)

    resultat = _resultat_demo(
        type_analyse="classement",
        elements_classes=[{"element": f"Produit {i}", "valeur": 100 - i} for i in range(12)],
        observations_aberrantes=[
            {"date": "2026-02-01", "valeur": 900.0, "ecart_zscore": 3.4},
            {"date": "2026-03-01", "valeur": 12.0, "ecart_zscore": 2.7},
        ],
    )
    utile = charge_utile(resultat)
    brut = json.dumps(utile, ensure_ascii=False, default=str)

    ok = True

    # a. Les series completes ne sortent pas : seulement leur cardinalite et leurs bornes.
    if "historique" in utile and set(utile["historique"]) == {"nb_points", "debut", "fin"}:
        print("  OK   serie historique reduite a nb_points/debut/fin")
    else:
        print("  ECHEC serie historique transmise en detail"); ok = False
    if '"valeur": 21.0' in brut:
        print("  ECHEC un point de la serie a fuite"); ok = False
    else:
        print("  OK   aucun point de la serie transmis")

    # b. Les observations aberrantes sont comptees, pas detaillees.
    if utile.get("anomalies") == {"nombre": 2, "ecart_max": 3.4}:
        print("  OK   anomalies reduites au nombre et a l'ecart max")
    else:
        print(f"  ECHEC anomalies transmises en detail : {utile.get('anomalies')}"); ok = False
    if "900.0" in brut:
        print("  ECHEC une valeur aberrante brute a fuite"); ok = False
    else:
        print("  OK   aucune valeur aberrante brute transmise")

    # c. Les agregats de classement sont plafonnes.
    tete = utile.get("elements_en_tete", [])
    if len(tete) == MAX_ELEMENTS_TRANSMIS and utile.get("nb_elements_total") == 12:
        print(f"  OK   classement plafonne a {MAX_ELEMENTS_TRANSMIS} agregats sur 12")
    else:
        print(f"  ECHEC plafond non applique : {len(tete)} elements"); ok = False

    # d. Liste blanche : aucune cle inattendue.
    autorisees = {
        "type_analyse", "operation", "valeur_analyse", "valeur_prevue", "unite",
        "modele_applique", "fiabilite", "indicateurs", "avertissements", "intervalle",
        "elements_en_tete", "nb_elements_total", "anomalies", "historique",
    }
    surplus = set(utile) - autorisees
    if surplus:
        print(f"  ECHEC cles hors liste blanche : {sorted(surplus)}"); ok = False
    else:
        print("  OK   aucune cle hors liste blanche")

    print(f"\n  Charge utile ({len(brut)} caracteres) :")
    print("   ", json.dumps(utile, ensure_ascii=False, indent=2, default=str).replace("\n", "\n    ")[:900])
    return ok


# ---------------------------------------------------------------------------
# 2. Le plafond de confiance
# ---------------------------------------------------------------------------

def _interpretation_demo(niveau: str) -> InterpretationRedigee:
    return InterpretationRedigee(
        synthese="Vos ventes pourraient progresser le mois prochain.",
        lecture="La projection ressort a 29,56 contre 25,00 aujourd'hui, mais l'intervalle "
                "de confiance couvre aussi bien une hausse qu'une baisse.",
        points_cles=["Projection a 29,56", "Intervalle [17,09 ; 42,03]"],
        recommandation="Attendez deux periodes supplementaires avant d'engager un reassort.",
        limite="Cette analyse ne permet pas d'affirmer un sens d'evolution.",
        niveau_confiance=niveau,
    )


def test_plafond() -> bool:
    print(TRAIT)
    print("PLAFOND DE CONFIANCE — ce qu'on accepte du modele, pas ce qu'on lui demande")
    print(TRAIT)
    ok = True

    cas = [
        # (indicateurs, fiabilite, annonce par le modele, attendu apres controle)
        ({"conclusif": False}, "bonne",   "eleve",  "faible"),
        ({"conclusif": False}, "bonne",   "modere", "faible"),
        ({"conclusif": True},  "limitee", "eleve",  "modere"),
        ({"conclusif": True},  "bonne",   "eleve",  "eleve"),
        ({"conclusif": True},  "bonne",   "faible", "faible"),   # jamais rehausse
    ]
    for indicateurs, fiab, annonce, attendu in cas:
        resultat = _resultat_demo(indicateurs=indicateurs, fiabilite={"code": fiab})
        interp = _interpretation_demo(annonce)
        ajust = _plafonner_confiance(interp, resultat)
        verdict = "OK  " if interp.niveau_confiance == attendu else "ECHEC"
        if interp.niveau_confiance != attendu:
            ok = False
        print(f"  {verdict} conclusif={str(indicateurs['conclusif']):5} fiabilite={fiab:8}"
              f" annonce={annonce:6} -> {interp.niveau_confiance:6} (attendu {attendu})"
              f"{'  [ajuste]' if ajust else ''}")

    print(f"\n  Plafond calcule sans le texte du modele : "
          f"cfg non conclusive -> {confiance_maximale(_resultat_demo())}")
    return ok


# ---------------------------------------------------------------------------
# 3. Indisponibilite du service
# ---------------------------------------------------------------------------

def test_indisponible() -> bool:
    print(TRAIT)
    print("INDISPONIBILITE — les chiffres restent, seule la redaction manque")
    print(TRAIT)
    resultat = _resultat_demo()
    sortie = interpreter_resultat(resultat, objectif_libelle="Prevoir une evolution")
    print(f"  cle API configuree : {llm_disponible()}")
    print(f"  disponible         : {sortie.disponible}")
    print(f"  motif              : {sortie.motif_indisponible}")
    print(f"  synthese           : {sortie.synthese}")
    print(f"  chiffres intacts   : valeur_prevue={resultat.valeur_prevue} "
          f"intervalle=[{resultat.intervalle_bas} ; {resultat.intervalle_haut}]")
    if sortie.disponible:
        print("\n  (cle presente — l'interpretation a ete produite, voir --config)")
        return True
    ok = sortie.interpretation is None and sortie.motif_indisponible
    print(f"\n  {'OK   ' if ok else 'ECHEC'} aucune interpretation fabriquee, motif conserve")
    return bool(ok)


# ---------------------------------------------------------------------------
# 4. Chaine complete sur une configuration reelle
# ---------------------------------------------------------------------------

def test_configuration(config_id: int, commit: bool) -> bool:
    from app.database.models.configuration_analyse import ConfigurationAnalyse
    from app.services.moteur_analyse import (
        executer_calcul,
        executer_et_stocker,
        extraire_donnees,
        intention_depuis_resume,
        preparer_donnees,
    )
    from app.services.moteur_analyse.interpretation import interpreter_et_stocker

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = SessionLocal()
    try:
        config = db.get(ConfigurationAnalyse, config_id)
        if config is None:
            print(f"Configuration {config_id} introuvable.")
            return False
        if not config.specification_json:
            print(f"Configuration {config_id} : aucune specification (traduction non aboutie).")
            return False

        print(TRAIT)
        print(f"CONFIGURATION {config_id} — chaine 4.1 -> 4.2 -> 4.4 -> 4.5")
        print(TRAIT)

        intention = intention_depuis_resume(json.loads(config.specification_json))
        extrait = extraire_donnees(db, config)
        prepare = preparer_donnees(extrait, config)
        # --commit passe par le chemin reel du lancement : c'est `executer_et_stocker`
        # qui cree la ligne ResultatAnalyse et renseigne `id_resultat`, sans quoi 4.5
        # n'aurait rien a annoter.
        if commit:
            calcul = executer_et_stocker(db, config, intention, prepare)
            if calcul is None:
                print(f"  Calcul impossible : {config.execution_erreur}")
                return False
        else:
            calcul = executer_calcul(intention, prepare.table, config_id)

        print(f"  operation      : {calcul.type_analyse}")
        print(f"  modele         : {calcul.modele_libelle}")
        print(f"  valeur         : {calcul.valeur_analyse}  prevue : {calcul.valeur_prevue}")
        print(f"  conclusif      : {calcul.indicateurs.get('conclusif')}")
        print(f"  fiabilite      : {(calcul.fiabilite or {}).get('code')}")
        print()

        if commit:
            sortie = interpreter_et_stocker(db, config, calcul)
        else:
            sortie = interpreter_resultat(
                calcul,
                objectif_libelle=config.objectif or None,
                besoin=config.besoin,
                reformulation=config.intention_reformulee,
            )

        if not sortie.disponible:
            print(f"  INTERPRETATION INDISPONIBLE — {sortie.motif_indisponible}")
            print("  (les chiffres ci-dessus restent consultables : c'est le comportement attendu)")
            if commit:
                _verifier_ligne(db, calcul.id_resultat)
            return True

        i = sortie.interpretation
        print(f"  Synthese       : {i.synthese}")
        print(f"  Confiance      : {i.niveau_confiance}")
        print(f"  Lecture        : {i.lecture}")
        print("  Points cles    :")
        for p in i.points_cles:
            print(f"      - {p}")
        print(f"  Recommandation : {i.recommandation}")
        print(f"  Limite         : {i.limite}")
        for a in sortie.ajustements:
            print(f"  [ajustement]   : {a}")
        if commit:
            _verifier_ligne(db, calcul.id_resultat)
        return True
    finally:
        db.close()


def _verifier_ligne(db, id_resultat) -> None:
    """Relit la ligne ecrite : la garantie « les chiffres restent » se verifie en base."""
    from app.database.models.resultat_analyse import ResultatAnalyse

    print(f"\n  Relecture de ResultatAnalyse {id_resultat} :")
    if id_resultat is None:
        print("    ECHEC aucune ligne creee")
        return
    db.expire_all()
    ligne = db.get(ResultatAnalyse, id_resultat)
    if ligne is None:
        print("    ECHEC ligne introuvable")
        return
    print(f"    valeur_analyse        : {ligne.valeur_analyse}")
    print(f"    valeur_prevue         : {ligne.valeur_prevue}")
    print(f"    intervalle            : [{ligne.intervalle_bas} ; {ligne.intervalle_haut}]")
    print(f"    modele_applique       : {ligne.modele_applique}")
    print(f"    interpretation_source : {ligne.interpretation_source}")
    print(f"    interpretation        : {(ligne.interpretation or '(absente)')[:80]}")
    print(f"    interpretation_erreur : {ligne.interpretation_erreur}")
    chiffres_ok = ligne.valeur_analyse is not None and ligne.resultat_json
    print(f"    {'OK   ' if chiffres_ok else 'ECHEC'} les chiffres sont consultables "
          f"independamment de l'interpretation")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    commit = "--commit" in args
    args = [a for a in args if a != "--commit"]

    if not args:
        print(__doc__)
        print(f"\nSchema de reponse transmis a l'API : "
              f"{list(schema_reponse().get('properties', {}))}")
        print(f"Cle API configuree : {llm_disponible()}")
        return

    mode = args[0]
    if mode == "--frontiere":
        sys.exit(0 if test_frontiere() else 1)
    if mode == "--plafond":
        sys.exit(0 if test_plafond() else 1)
    if mode == "--indispo":
        sys.exit(0 if test_indisponible() else 1)
    if mode == "--tout":
        r = [test_frontiere(), test_plafond(), test_indisponible()]
        print(TRAIT)
        print(f"RESULTAT : {sum(r)}/{len(r)} series passees")
        sys.exit(0 if all(r) else 1)
    if mode.isdigit():
        sys.exit(0 if test_configuration(int(mode), commit) else 1)
    print(__doc__)


if __name__ == "__main__":
    main()
