"""Module 6 — vues SQL à plat pour Power BI Desktop.

Crée 7 vues exposant les résultats d'analyse en colonnes plates, prêtes à être
consommées sans transformation. Power BI ne sait pas exploiter du JSON imbriqué :
les valeurs sont donc extraites de `resultat_json` par les vues, jamais exposées
sous forme brute.

⚠️ **Aucune table n'est modifiée ni créée.** Uniquement des vues.

Idempotent : `CREATE OR REPLACE VIEW`. Relançable autant de fois que nécessaire.

    python migrate_vues_powerbi.py              # crée ou remplace les vues
    python migrate_vues_powerbi.py --verifier   # contrôle vues + droits, n'écrit rien
    python migrate_vues_powerbi.py --supprimer  # retire les vues

⚠️ **Le SGBD est MariaDB, pas MySQL.** `JSON_TABLE` — la façon standard d'éclater
un tableau JSON en lignes — n'existe qu'à partir de MariaDB 10.6. Le contournement
retenu est le moteur **SEQUENCE** (`seq_0_to_N`), une table virtuelle sans stockage
disponible en 10.4 et utilisable à l'intérieur d'une vue. Aucune table d'appoint
n'est donc nécessaire.

⚠️ **La borne de la séquence tronque en SILENCE.** Mesuré : avec `seq_0_to_999`,
une série de 5 000 points rend 1 000 lignes **sans lever d'erreur**. La borne est
donc portée à `LIMITE_POINTS_SERIE` (10 000) et, surtout, `vw_pbi_resultats` porte
une colonne `serie_tronquee` : un rapport qui perdrait des points le dit au lieu de
mentir. Voir docs/POWERBI.md pour le détail des mesures.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import inspect, text

from app.database.connection import engine

# La console Windows est en cp1252 : sans cela, un accent dans un libellé d'objectif
# fait échouer l'affichage, pas la migration — mais on ne verrait plus le résultat.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# Le journal SQL de SQLAlchemy noierait la sortie : ce script rend son propre compte.
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
engine.echo = False

# Borne du moteur SEQUENCE. Au-delà, l'éclatement perdrait des points sans le dire —
# `serie_tronquee` rend ce cas visible. Coût nul sur les séries courantes : mesuré
# à 5 ms sur les données réelles, identique à une borne de 1 000.
LIMITE_POINTS_SERIE = 10_000

# Tables qui ne doivent JAMAIS être atteintes par le compte Power BI, ni directement
# ni au travers d'une vue. Contrôlées par `--verifier`.
TABLES_INTERDITES = ("Utilisateur", "ConnexionBDD", "PasswordResetToken", "Administrateur")

UTILISATEUR_PBI = "powerbi"


def _libelles_objectif() -> str:
    """Expression CASE des libellés d'objectif, **dérivée de la source unique**.

    Les libellés vivent dans `_OBJECTIFS` (routes/dashboard.py). Les recopier ici
    en dur les ferait diverger au premier renommage : ils sont donc lus et
    transformés en SQL au moment de la migration.
    """
    from app.routes.dashboard import _OBJECTIF_LABELS

    branches = "\n        ".join(
        f"WHEN c.objectif = '{val}' THEN '{lib}'" for val, lib in _OBJECTIF_LABELS.items()
    )
    return f"""CASE
        {branches}
        WHEN c.objectif IS NULL OR c.objectif = '' THEN 'Besoin exprimé librement'
        ELSE c.objectif
    END"""


# ──────────────────────────────────────────────────────────────────────────────
# Fragments partagés
# ──────────────────────────────────────────────────────────────────────────────

# La chaîne d'appartenance, identique partout dans l'application :
# Résultat → Configuration → Import → Source → Entreprise.
CHAINE = """
    JOIN ConfigurationAnalyse c ON r.id_configuration = c.id_configuration
    JOIN ImportDonnee i         ON c.id_import = i.id_import
    JOIN SourceDonnee s         ON i.id_source = s.id_source
    JOIN Entreprise e           ON s.idEntreprise = e.idEntreprise
"""


def _txt(chemin: str, source: str = "r.resultat_json") -> str:
    """Valeur texte extraite du JSON, déjà déguillemetée."""
    return f"JSON_UNQUOTE(JSON_EXTRACT({source}, '{chemin}'))"


def _num(chemin: str, source: str = "r.resultat_json") -> str:
    """Valeur numérique. `NULLIF(...,'null')` évite qu'un null JSON devienne 0."""
    return f"CAST(NULLIF({_txt(chemin, source)}, 'null') AS DECIMAL(20,6))"


def _point(tableau: str, champ: str, cast: str = "texte") -> str:
    """Champ du n-ième élément d'un tableau JSON, l'index venant de la séquence."""
    chemin = f"CONCAT('$.visualisation.{tableau}[', q.seq, '].{champ}')"
    brut = f"JSON_UNQUOTE(JSON_EXTRACT(r.resultat_json, {chemin}))"
    if cast == "nombre":
        return f"CAST(NULLIF({brut}, 'null') AS DECIMAL(20,6))"
    if cast == "entier":
        return f"CAST(NULLIF({brut}, 'null') AS SIGNED)"
    if cast == "date":
        return f"CAST(NULLIF({brut}, 'null') AS DATETIME)"
    return brut


def _serie(tableau: str, nature: str, avec_bornes: bool) -> str:
    """Un SELECT éclatant un tableau de points en lignes."""
    bornes = (f"{_point(tableau, 'bas', 'nombre')} AS borne_basse,\n"
              f"        {_point(tableau, 'haut', 'nombre')} AS borne_haute"
              if avec_bornes else "        NULL AS borne_basse,\n        NULL AS borne_haute")
    return f"""
    SELECT
        r.id_resultat,
        e.idEntreprise                       AS id_entreprise,
        c.id_configuration,
        q.seq                                AS position,
        {_point(tableau, 'date', 'date')}    AS date_point,
        {_point(tableau, 'valeur', 'nombre')} AS valeur,
        '{nature}'                           AS nature,
{bornes}
    FROM ResultatAnalyse r
    {CHAINE}
    JOIN seq_0_to_{LIMITE_POINTS_SERIE - 1} q
      ON q.seq < JSON_LENGTH(r.resultat_json, '$.visualisation.{tableau}')
"""


# ──────────────────────────────────────────────────────────────────────────────
# Définition des vues
# ──────────────────────────────────────────────────────────────────────────────

def definitions() -> dict[str, str]:
    objectif = _libelles_objectif()
    return {
        # ── 1. Dimension entreprise ────────────────────────────────────────────
        "vw_pbi_entreprises": """
            SELECT
                e.idEntreprise        AS id_entreprise,
                e.nom                 AS entreprise,
                e.secteur_activite    AS secteur,
                e.date_inscription    AS date_inscription,
                e.statut_demande      AS statut_demande
            FROM Entreprise e
        """,

        # ── 2. Résultats : une ligne par exécution ─────────────────────────────
        "vw_pbi_resultats": f"""
            SELECT
                r.id_resultat,
                e.idEntreprise                       AS id_entreprise,
                e.nom                                AS entreprise,
                c.id_configuration,
                {objectif}                           AS objectif,
                r.type_analyse,
                r.date_execution,
                c.frequence,
                s.nom                                AS source,
                s.type_source,

                CAST(NULLIF(r.valeur_analyse, '') AS DECIMAL(20,6))  AS valeur_analysee,
                CAST(NULLIF(r.valeur_prevue, '')  AS DECIMAL(20,6))  AS valeur_prevue,
                r.intervalle_bas,
                r.intervalle_haut,
                {_txt('$.intervalle.methode')}       AS methode_intervalle,
                {_txt('$.unite')}                    AS unite,

                r.modele_applique,
                {_txt('$.modele_libelle')}           AS modele_libelle,
                r.fiabilite,
                {_txt('$.fiabilite.label')}          AS fiabilite_libelle,
                {_num('$.fiabilite.points')}         AS points,

                r.criticite,
                r.criticite_rang,
                {_txt('$.criticite.libelle')}        AS criticite_libelle,
                CASE WHEN {_txt('$.indicateurs.conclusif')} = 'true' THEN 1 ELSE 0 END
                                                     AS conclusif,
                {_txt('$.indicateurs.sens')}         AS sens,

                -- Indicateurs calculés ici plutôt qu'en DAX : une formule écrite une
                -- fois en SQL ne peut pas diverger d'un rapport à l'autre.
                CAST(NULLIF(r.valeur_prevue, '') AS DECIMAL(20,6))
                  - CAST(NULLIF(r.valeur_analyse, '') AS DECIMAL(20,6))  AS ecart_observe_prevu,
                CASE WHEN CAST(NULLIF(r.valeur_analyse, '') AS DECIMAL(20,6)) <> 0
                     THEN ROUND(100 * (CAST(NULLIF(r.valeur_prevue, '') AS DECIMAL(20,6))
                                     - CAST(NULLIF(r.valeur_analyse, '') AS DECIMAL(20,6)))
                                    / ABS(CAST(NULLIF(r.valeur_analyse, '') AS DECIMAL(20,6))), 2)
                END                                                      AS ecart_pct,
                (r.intervalle_haut - r.intervalle_bas)                   AS largeur_intervalle,
                CASE WHEN CAST(NULLIF(r.valeur_prevue, '') AS DECIMAL(20,6)) <> 0
                     THEN ROUND((r.intervalle_haut - r.intervalle_bas)
                                / ABS(CAST(NULLIF(r.valeur_prevue, '') AS DECIMAL(20,6))), 4)
                END                                                      AS largeur_relative,
                DATEDIFF(NOW(), r.date_execution)                        AS age_execution_jours,

                JSON_LENGTH(r.resultat_json, '$.visualisation.serie_historique')
                                                     AS nb_points_historique,
                JSON_LENGTH(r.resultat_json, '$.visualisation.serie_prevue')
                                                     AS nb_points_prevus,
                -- ⚠️ Garde-fou : au-delà de la borne du moteur SEQUENCE, les vues de
                -- série perdraient des points SANS erreur. Ce drapeau rend la
                -- troncature visible dans le rapport.
                CASE WHEN JSON_LENGTH(r.resultat_json, '$.visualisation.serie_historique')
                          > {LIMITE_POINTS_SERIE} THEN 1 ELSE 0 END      AS serie_tronquee
            FROM ResultatAnalyse r
            {CHAINE}
        """,

        # ── 3. Séries : une ligne par point, observé et prévu réunis ───────────
        # L'union en une seule vue est délibérée : Power BI trace une courbe
        # continue en découpant sur `nature`, là où deux vues imposeraient une
        # relation supplémentaire pour rien.
        "vw_pbi_series": (
            _serie("serie_historique", "observe", avec_bornes=False)
            + " UNION ALL "
            + _serie("serie_prevue", "prevu", avec_bornes=True)
        ),

        # ── 4. Classement : une ligne par élément ──────────────────────────────
        "vw_pbi_classement": f"""
            SELECT
                r.id_resultat,
                e.idEntreprise                            AS id_entreprise,
                c.id_configuration,
                r.date_execution,
                {_point('elements_classes', 'rang', 'entier')}    AS rang,
                {_point('elements_classes', 'element')}           AS element,
                {_point('elements_classes', 'valeur', 'nombre')}  AS valeur,
                -- Part de l'élément dans le total du classement : l'indicateur que
                -- tout rapport recalculerait autrement en DAX.
                CASE WHEN CAST(NULLIF(r.valeur_analyse, '') AS DECIMAL(20,6)) <> 0
                     THEN ROUND(100 * {_point('elements_classes', 'valeur', 'nombre')}
                                / CAST(NULLIF(r.valeur_analyse, '') AS DECIMAL(20,6)), 2)
                END                                               AS part_pct
            FROM ResultatAnalyse r
            {CHAINE}
            JOIN seq_0_to_{LIMITE_POINTS_SERIE - 1} q
              ON q.seq < JSON_LENGTH(r.resultat_json, '$.visualisation.elements_classes')
        """,

        # ── 5. Anomalies : une ligne par observation aberrante ─────────────────
        "vw_pbi_anomalies": f"""
            SELECT
                r.id_resultat,
                e.idEntreprise                                          AS id_entreprise,
                c.id_configuration,
                r.date_execution,
                {_point('observations_aberrantes', 'date', 'date')}     AS date_observation,
                {_point('observations_aberrantes', 'valeur', 'nombre')} AS valeur,
                {_point('observations_aberrantes', 'ecart_zscore', 'nombre')} AS ecart_zscore,
                {_point('observations_aberrantes', 'sens')}             AS sens,
                {_num('$.indicateurs.moyenne')}                         AS moyenne_serie,
                {_num('$.indicateurs.ecart_type')}                      AS ecart_type_serie,
                {_num('$.indicateurs.seuil_ecarts_types')}              AS seuil_ecarts_types
            FROM ResultatAnalyse r
            {CHAINE}
            JOIN seq_0_to_{LIMITE_POINTS_SERIE - 1} q
              ON q.seq < JSON_LENGTH(r.resultat_json, '$.visualisation.observations_aberrantes')
        """,

        # ── 6. Alertes ─────────────────────────────────────────────────────────
        "vw_pbi_alertes": """
            SELECT
                a.id_alerte,
                e.idEntreprise                AS id_entreprise,
                e.nom                         AS entreprise,
                a.id_configuration,
                a.id_resultat,
                a.type_alerte,
                a.niveau,
                a.criticite_rang,
                a.date_creation,
                a.statut,
                a.date_traitement,
                -- Délai de traitement et ancienneté : deux colonnes qui distinguent
                -- une entreprise réactive d'une entreprise qui laisse traîner.
                TIMESTAMPDIFF(HOUR, a.date_creation, a.date_traitement) AS delai_traitement_h,
                CASE WHEN a.date_traitement IS NULL
                     THEN DATEDIFF(NOW(), a.date_creation) END          AS age_ouverte_jours,
                CASE WHEN a.date_traitement IS NULL THEN 1 ELSE 0 END   AS est_ouverte
            FROM Alerte a
            JOIN ConfigurationAnalyse c ON a.id_configuration = c.id_configuration
            JOIN ImportDonnee i         ON c.id_import = i.id_import
            JOIN SourceDonnee s         ON i.id_source = s.id_source
            JOIN Entreprise e           ON s.idEntreprise = e.idEntreprise
        """,

        # ── 7. Activité : imports et configurations, un événement par ligne ────
        "vw_pbi_activite": """
            SELECT
                'import'            AS type_evenement,
                i.id_import         AS id_evenement,
                s.idEntreprise      AS id_entreprise,
                e.nom               AS entreprise,
                i.date_import       AS date_evenement,
                s.nom               AS source,
                s.type_source,
                i.nom_fichier       AS libelle,
                i.statut
            FROM ImportDonnee i
            JOIN SourceDonnee s ON i.id_source = s.id_source
            JOIN Entreprise e   ON s.idEntreprise = e.idEntreprise
            UNION ALL
            SELECT
                'configuration'     AS type_evenement,
                c.id_configuration  AS id_evenement,
                s.idEntreprise      AS id_entreprise,
                e.nom               AS entreprise,
                c.date_creation     AS date_evenement,
                s.nom               AS source,
                s.type_source,
                NULL                AS libelle,
                c.statut
            FROM ConfigurationAnalyse c
            JOIN ImportDonnee i ON c.id_import = i.id_import
            JOIN SourceDonnee s ON i.id_source = s.id_source
            JOIN Entreprise e   ON s.idEntreprise = e.idEntreprise
        """,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Exécution
# ──────────────────────────────────────────────────────────────────────────────

def creer() -> int:
    vues = definitions()
    echecs = 0
    with engine.begin() as cx:
        for nom, corps in vues.items():
            try:
                cx.execute(text(f"CREATE OR REPLACE VIEW `{nom}` AS {corps}"))
                print(f"  OK    {nom}")
            except Exception as exc:
                echecs += 1
                print(f"  ECHEC {nom} -> {str(exc)[:200]}")
    return echecs


def supprimer() -> None:
    with engine.begin() as cx:
        for nom in definitions():
            cx.execute(text(f"DROP VIEW IF EXISTS `{nom}`"))
            print(f"  retirée : {nom}")


def verifier() -> int:
    """Contrôle que les vues répondent et que rien de sensible n'en sort."""
    echecs = 0
    inspecteur = inspect(engine)
    existantes = set(inspecteur.get_view_names())

    print("--- présence et volume ---")
    with engine.connect() as cx:
        for nom in definitions():
            if nom not in existantes:
                print(f"  ECHEC {nom} : absente")
                echecs += 1
                continue
            try:
                n = cx.execute(text(f"SELECT COUNT(*) FROM `{nom}`")).scalar()
                cols = [c["name"] for c in inspecteur.get_columns(nom)]
                print(f"  OK    {nom:<22} {n:>5} ligne(s) · {len(cols)} colonnes")
            except Exception as exc:
                echecs += 1
                print(f"  ECHEC {nom} : {str(exc)[:150]}")

        print("\n--- aucune colonne sensible exposée ---")
        interdits = ("mot_de_passe", "password", "token", "chiffre", "secret")
        for nom in definitions():
            if nom not in existantes:
                continue
            cols = [c["name"].lower() for c in inspecteur.get_columns(nom)]
            fuites = [c for c in cols if any(mot in c for mot in interdits)]
            if fuites:
                echecs += 1
                print(f"  ECHEC {nom} expose {fuites}")
        if not echecs:
            print("  OK    aucune colonne de mot de passe, de jeton ni de secret")

        print("\n--- droits du compte Power BI ---")
        try:
            lignes = cx.execute(text(f"SHOW GRANTS FOR '{UTILISATEUR_PBI}'@'%'")).fetchall()
            octroyes = [l[0] for l in lignes]
            for g in octroyes:
                print(f"     {g}")
            # Un GRANT sur une table (ou sur *) donnerait accès aux mots de passe.
            large = [g for g in octroyes
                     if ".*" in g.replace("`", "") and "USAGE" not in g]
            if large:
                echecs += 1
                print(f"  ECHEC droits trop larges : {large}")
            for table in TABLES_INTERDITES:
                if any(table.lower() in g.lower() for g in octroyes):
                    echecs += 1
                    print(f"  ECHEC droit accordé sur la table sensible {table}")
            if not large:
                print("  OK    aucun droit au niveau base ni sur une table sensible")
        except Exception:
            print(f"  (compte '{UTILISATEUR_PBI}' absent — voir docs/POWERBI.md pour le créer)")

    return echecs


if __name__ == "__main__":
    if "--supprimer" in sys.argv:
        supprimer()
        sys.exit(0)
    if "--verifier" in sys.argv:
        sys.exit(1 if verifier() else 0)

    print(f"Création des vues Power BI (borne de série : {LIMITE_POINTS_SERIE} points)\n")
    echecs = creer()
    print()
    echecs += verifier()
    print(f"\n{'TERMINÉ' if not echecs else str(echecs) + ' PROBLÈME(S)'}")
    sys.exit(1 if echecs else 0)
