"""
Renseigne la criticite (Module 4, tache 4.6) sur les resultats existants.

Deux modes, complementaires :

  --recalcul   (defaut) Recalcule la criticite des lignes ResultatAnalyse deja en base,
               a partir de leur resultat_json. AUCUNE relecture des donnees sources,
               aucun appel LLM : la criticite est une fonction pure des indicateurs, donc
               rejouable a l'identique. C'est le seul mode qui renseigne les lignes
               ANCIENNES — relancer une configuration en cree une nouvelle, il ne corrige
               pas la precedente.

  --relancer   Rejoue la chaine 4.1 -> 4.2 -> 4.4 sur chaque configuration executable et
               ECRIT UNE NOUVELLE LIGNE. C'est le seul moyen d'obtenir le bloc statistique
               complet (moyenne, ecart_type, erreur_ajustement...), qui exige de relire les
               donnees. L'interpretation (4.5) n'est pas appelee : elle consommerait du
               quota LLM sans rien apporter a la criticite.

Les deux modes sont sans effet de bord s'ils sont relances.

Usage :
    python recalculer_criticite.py                 # apercu, n'ecrit rien
    python recalculer_criticite.py --commit        # recalcul des lignes existantes
    python recalculer_criticite.py --relancer --commit
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.database.connection import SessionLocal, engine

engine.echo = False
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.resultat_analyse import ResultatAnalyse
from app.services.moteur_analyse import evaluer_criticite
# Reutilisation deliberee du calcul de 4.4 plutot qu'une copie : une concentration
# recalculee autrement donnerait un verdict different de celui d'un lancement normal.
from app.services.moteur_analyse.execution import _concentration

TRAIT = "-" * 88


def _completer_indicateurs(resume: dict) -> dict:
    """Ajoute les indicateurs de 4.6 absents des resultats anterieurs.

    Ils sont reconstitues depuis les series de visualisation deja stockees — jamais
    inventes. Ceux qui exigeraient de relire les donnees sources (moyenne, ecart-type,
    erreur d'ajustement) ne sont PAS ajoutes ici : c'est le role de --relancer.
    """
    ind = dict(resume.get("indicateurs") or {})
    vis = resume.get("visualisation") or {}
    type_analyse = resume.get("type_analyse")

    if type_analyse == "classement" and ind.get("concentration") is None:
        elements = vis.get("elements_classes") or []
        if elements:
            ind["concentration"] = _concentration(elements)

    if type_analyse == "anomalie" and ind.get("ecart_max") is None:
        obs = vis.get("observations_aberrantes") or []
        # Les lignes anterieures au renommage portent « ecart_type » ; les recentes
        # « ecart_zscore ». On accepte les deux plutot que d'ignorer les anciennes.
        ecarts = [
            abs(float(o[cle]))
            for o in obs
            for cle in ("ecart_zscore", "ecart_type")
            if isinstance(o.get(cle), (int, float))
        ]
        ind["ecart_max"] = max(ecarts) if ecarts else None
    return ind


def recalculer(db, commit: bool) -> None:
    lignes = db.query(ResultatAnalyse).order_by(ResultatAnalyse.id_resultat).all()
    print(TRAIT)
    print(f"RECALCUL sur {len(lignes)} ligne(s) de resultat existante(s)")
    print(TRAIT)
    print(f"{'id':>4} {'cfg':>4} {'type':<11} {'avant':<10} {'apres':<10} motif")

    faits = ignores = 0
    for ligne in lignes:
        if not ligne.resultat_json:
            print(f"{ligne.id_resultat:>4} {ligne.id_configuration:>4} "
                  f"{(ligne.type_analyse or '?'):<11} {'-':<10} {'IGNOREE':<10} "
                  "aucun resultat_json : rien a recalculer")
            ignores += 1
            continue
        try:
            resume = json.loads(ligne.resultat_json)
        except Exception as exc:
            print(f"{ligne.id_resultat:>4} {ligne.id_configuration:>4} "
                  f"{(ligne.type_analyse or '?'):<11} {'-':<10} {'IGNOREE':<10} "
                  f"resultat_json illisible ({exc})")
            ignores += 1
            continue

        indicateurs = _completer_indicateurs(resume)
        criticite = evaluer_criticite(
            resume.get("type_analyse") or ligne.type_analyse,
            indicateurs,
            resume.get("fiabilite") or {},
        )
        avant = ligne.criticite or "(vide)"
        print(f"{ligne.id_resultat:>4} {ligne.id_configuration:>4} "
              f"{(ligne.type_analyse or '?'):<11} {avant:<10} {criticite['niveau']:<10} "
              f"{criticite['motif']}")

        if commit:
            ligne.criticite = criticite["niveau"]
            ligne.criticite_rang = criticite["rang"]
            ligne.criticite_motif = criticite["motif"]
            # Le resume stocke reste la source du detail : on y remet les indicateurs
            # completes et la justification, sinon la colonne et le JSON divergeraient.
            resume["indicateurs"] = {**indicateurs, "criticite": criticite}
            resume["criticite"] = criticite
            ligne.resultat_json = json.dumps(resume, ensure_ascii=False)
        faits += 1

    if commit:
        db.commit()
    print(f"\n{faits} ligne(s) recalculee(s), {ignores} ignoree(s)"
          f"{'' if commit else '  — APERCU, rien ecrit (ajouter --commit)'}")


def relancer(db, commit: bool) -> None:
    from app.services.moteur_analyse import (
        executer_calcul,
        executer_et_stocker,
        extraire_donnees,
        intention_depuis_resume,
        preparer_donnees,
    )

    configs = db.query(ConfigurationAnalyse).order_by(
        ConfigurationAnalyse.id_configuration
    ).all()
    print("\n" + TRAIT)
    print(f"RELANCE de {len(configs)} configuration(s) — chaine 4.1 -> 4.2 -> 4.4")
    print("Une NOUVELLE ligne de resultat est creee par configuration relancee.")
    print(TRAIT)

    ok = echecs = sautees = 0
    for config in configs:
        cid = config.id_configuration
        if not config.specification_json:
            print(f"  cfg {cid:>3} : SAUTEE — aucune specification (traduction non aboutie)")
            sautees += 1
            continue
        try:
            intention = intention_depuis_resume(json.loads(config.specification_json))
            prepare = preparer_donnees(extraire_donnees(db, config), config)
            if commit:
                calcul = executer_et_stocker(db, config, intention, prepare)
                if calcul is None:
                    print(f"  cfg {cid:>3} : ECHEC — {config.execution_erreur}")
                    echecs += 1
                    continue
            else:
                calcul = executer_calcul(intention, prepare.table, cid)
            c = calcul.criticite
            print(f"  cfg {cid:>3} : OK  {calcul.type_analyse:<11} "
                  f"{c['niveau']:<10} (rang {c['rang']}) — {c['motif']}")
            for plafond in c["plafonnements"]:
                print(f"           {plafond}")
            ok += 1
        except Exception as exc:
            print(f"  cfg {cid:>3} : ECHEC — {exc}")
            echecs += 1

    print(f"\n{ok} relancee(s), {echecs} en echec, {sautees} sautee(s)"
          f"{'' if commit else '  — APERCU, rien ecrit (ajouter --commit)'}")


def main() -> None:
    commit = "--commit" in sys.argv
    mode_relance = "--relancer" in sys.argv

    db = SessionLocal()
    try:
        if mode_relance:
            relancer(db, commit)
        else:
            recalculer(db, commit)
    finally:
        db.close()


if __name__ == "__main__":
    main()
