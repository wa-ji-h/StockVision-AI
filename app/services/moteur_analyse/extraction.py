"""Module 4 — tâche 4.1 : extraction des données sélectionnées.

Consomme une `ConfigurationAnalyse` (les données réelles sont relues à la source, le
`payload_execution` produit par « Lancer l'analyse » ne sert que de trace) et produit un
`ResultatExtraction` contenant un DataFrame pandas par table sélectionnée.

Garantie centrale : **aucune donnée non sélectionnée n'est extraite, même temporairement**.
- BDD  : le SELECT ne nomme que les colonnes retenues (jamais `SELECT *`).
- CSV  : `read_csv(usecols=...)` ne matérialise que les colonnes retenues.
- SQL  : le script est parsé, mais seules les colonnes retenues sont conservées dans le DataFrame.

Toute erreur métier remonte en `ExtractionError` porteuse d'un message en français destiné à
l'entreprise — aucune exception brute (pymysql, pandas, SQLAlchemy) ne traverse ce module.

La suite (4.2 préparation) consomme `ResultatExtraction.dataframe` / `.donnees` directement.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from app.core.config import settings
from app.database.models.connexion_bdd import ConnexionBDD
from app.database.models.import_donnee import ImportDonnee
from app.database.models.source_donnee import SourceDonnee

# app/ — extraction.py vit dans app/services/moteur_analyse/
_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(_APP_DIR, "uploads")

# Statuts d'exécution écrits par ce module (les autres appartiennent au Module 3).
STATUT_EXTRACTION_EN_COURS = "extraction_en_cours"
STATUT_ERREUR_DONNEES = "erreur_donnees"

# Au-delà, on tronque : 4.1 sert à préparer une analyse, pas à rapatrier une base entière.
LIMITE_LIGNES = 100_000


class ExtractionError(Exception):
    """Erreur d'extraction porteuse d'un message lisible par l'entreprise."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []

    def message_complet(self) -> str:
        if not self.details:
            return self.message
        return f"{self.message} ({', '.join(self.details)})"


@dataclass
class ResultatExtraction:
    """Sortie de 4.1, entrée de 4.2."""

    config_id: int
    source_id: int
    source_nom: str
    source_type: str  # BDD | CSV | SQL
    donnees: dict[str, pd.DataFrame]
    avertissements: list[str] = field(default_factory=list)

    @property
    def dataframe(self) -> pd.DataFrame:
        """Le DataFrame unique quand une seule table est sélectionnée (cas courant).

        Avec plusieurs tables, il n'existe pas de jointure évidente : 4.2 doit alors
        piocher explicitement dans `donnees`.
        """
        if len(self.donnees) == 1:
            return next(iter(self.donnees.values()))
        raise ExtractionError(
            f"Cette configuration porte sur {len(self.donnees)} tables "
            f"({', '.join(sorted(self.donnees))}) : précisez laquelle analyser."
        )

    def resume(self) -> dict:
        """Résumé sérialisable (log, futur affichage, trace d'exécution)."""
        return {
            "config_id": self.config_id,
            "source": {"id": self.source_id, "nom": self.source_nom, "type": self.source_type},
            "tables": {
                nom: {
                    "lignes": int(len(df)),
                    "colonnes": list(df.columns),
                    "types": {c: str(t) for c, t in df.dtypes.items()},
                }
                for nom, df in self.donnees.items()
            },
            "avertissements": self.avertissements,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Accès à la source
# ──────────────────────────────────────────────────────────────────────────────

def _build_db_url(type_sgbd: str, username: str, password: str, host: str, port: int, db_name: str) -> str:
    """Miroir de `_build_db_url` de app/routes/dashboard.py (dupliqué pour éviter
    un import circulaire routes ↔ services)."""
    from urllib.parse import quote_plus

    u, p = quote_plus(username), quote_plus(password)
    if type_sgbd == "mysql":
        return f"mysql+pymysql://{u}:{p}@{host}:{port}/{db_name}"
    if type_sgbd == "postgresql":
        return f"postgresql+psycopg2://{u}:{p}@{host}:{port}/{db_name}"
    return f"mssql+pyodbc://{u}:{p}@{host}:{port}/{db_name}?driver=ODBC+Driver+17+for+SQL+Server"


def _ouvrir_connexion_source(db, source: SourceDonnee):
    """Crée un engine SQLAlchemy vers la base tierce. À disposer par l'appelant."""
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine

    connexion = db.query(ConnexionBDD).filter(ConnexionBDD.id_source == source.id_source).first()
    if not connexion:
        raise ExtractionError(f"La connexion de la source « {source.nom} » est introuvable.")

    if not settings.DB_ENCRYPTION_KEY:
        raise ExtractionError(
            "La clé de chiffrement des connexions (DB_ENCRYPTION_KEY) n'est pas configurée sur le serveur."
        )
    try:
        f = Fernet(settings.DB_ENCRYPTION_KEY.encode())
        username = f.decrypt(connexion.user_chiffre.encode()).decode()
        password = f.decrypt(connexion.password_chiffre.encode()).decode()
    except Exception:
        raise ExtractionError(
            f"Les identifiants de la source « {source.nom} » n'ont pas pu être déchiffrés. "
            "Reconfigurez la connexion."
        )

    url = _build_db_url(
        connexion.type_sgbd, username, password, connexion.host, connexion.port, connexion.db_name
    )
    try:
        return create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
    except Exception as exc:
        raise ExtractionError(f"Connexion impossible à « {source.nom} » : {exc}")


def _chemin_fichier(imp: ImportDonnee, source: SourceDonnee) -> str:
    """Chemin disque du fichier importé. Une source fichier est autonome : elle est relue
    ici depuis le stockage serveur, sans jamais passer par la base de l'entreprise."""
    if not imp.chemin_fichier:
        raise ExtractionError(
            f"Le fichier « {imp.nom_fichier or source.nom} » n'a pas été conservé sur le serveur "
            "(import antérieur à l'archivage des fichiers). Réimportez-le depuis « Importer des données »."
        )
    chemin = os.path.join(UPLOAD_DIR, imp.chemin_fichier)
    if not os.path.isfile(chemin):
        raise ExtractionError(
            f"Le fichier importé « {imp.nom_fichier} » est introuvable sur le serveur. Réimportez-le."
        )
    return chemin


# ──────────────────────────────────────────────────────────────────────────────
# Extraction — base de données tierce
# ──────────────────────────────────────────────────────────────────────────────

def _extraire_bdd(db, source: SourceDonnee, selection: dict) -> dict[str, pd.DataFrame]:
    """SELECT restreint aux colonnes retenues, une requête par table.

    Les identifiants de tables/colonnes ne sont jamais concaténés dans du SQL : on les
    résout contre les métadonnées réelles puis on laisse SQLAlchemy construire et quoter
    la requête (aucune surface d'injection). Seuls des SELECT sont émis, sans commit.
    """
    from sqlalchemy import MetaData, Table, select

    engine = _ouvrir_connexion_source(db, source)
    resultats: dict[str, pd.DataFrame] = {}
    try:
        from sqlalchemy import inspect as sa_inspect

        try:
            tables_reelles = set(sa_inspect(engine).get_table_names())
        except Exception as exc:
            raise ExtractionError(f"Lecture impossible de la structure de « {source.nom} » : {exc}")

        for nom_table, colonnes in selection.items():
            if nom_table not in tables_reelles:
                raise ExtractionError(
                    f"La table « {nom_table} » n'existe plus dans la base « {source.nom} »."
                )

            metadata = MetaData()
            try:
                table = Table(nom_table, metadata, autoload_with=engine)
            except Exception as exc:
                raise ExtractionError(f"Lecture impossible de la table « {nom_table} » : {exc}")

            colonnes_reelles = list(table.columns.keys())
            attendues = colonnes_reelles if _est_selection_totale(colonnes) else list(colonnes)

            manquantes = [c for c in attendues if c not in table.columns]
            if manquantes:
                raise ExtractionError(
                    f"Colonnes absentes de la table « {nom_table} » : {', '.join(manquantes)}."
                )

            requete = select(*[table.c[c] for c in attendues]).limit(LIMITE_LIGNES + 1)
            try:
                with engine.connect() as conn:
                    df = pd.read_sql(requete, conn)
            except Exception as exc:
                raise ExtractionError(f"Lecture impossible des données de « {nom_table} » : {exc}")

            resultats[nom_table] = df
    finally:
        engine.dispose()

    return resultats


# ──────────────────────────────────────────────────────────────────────────────
# Extraction — fichier CSV
# ──────────────────────────────────────────────────────────────────────────────

def _colonnes_csv_retenues(selection: dict, colonnes_fichier: list[str], nom_fichier: str) -> list[str]:
    """Résout la sélection d'un CSV en liste de colonnes réelles.

    Le wizard écrit `{colonne: "*"}`, d'anciennes configurations `{fichier: [colonnes]}`.
    Plutôt que de deviner d'après la forme, on confronte les deux lectures à l'en-tête réel
    du fichier et on retient celle qui correspond. Déterministe et auto-corrigeant.
    """
    lecture_plate = list(selection)
    lecture_imbriquee = [c for v in selection.values() if isinstance(v, list) for c in v]

    if lecture_plate and all(c in colonnes_fichier for c in lecture_plate):
        return lecture_plate
    if lecture_imbriquee and all(c in colonnes_fichier for c in lecture_imbriquee):
        return lecture_imbriquee
    if len(selection) == 1 and all(_est_selection_totale(v) for v in selection.values()):
        # {fichier: "*"} — le fichier entier a été retenu sans détail de colonnes
        return colonnes_fichier

    inconnues = sorted({c for c in lecture_plate + lecture_imbriquee if c not in colonnes_fichier})
    raise ExtractionError(
        f"Colonnes absentes du fichier « {nom_fichier} » : {', '.join(inconnues)}. "
        f"Colonnes disponibles : {', '.join(colonnes_fichier)}. "
        "Réimportez le fichier ou modifiez la configuration."
    )


def _extraire_csv(imp: ImportDonnee, source: SourceDonnee, selection: dict) -> dict[str, pd.DataFrame]:
    """Un CSV est une structure tabulaire unique : seules des colonnes sont sélectionnables.

    Le wizard les liste à plat, donc la sélection vaut normalement `{colonne: "*"}`. D'anciennes
    configurations peuvent porter la forme `{fichier: [colonnes]}`. Plutôt que de deviner d'après
    la forme, on confronte les deux lectures à l'en-tête réel du fichier : celle qui correspond
    est la bonne. Déterministe, et auto-corrigeant si le wizard change.
    """
    chemin = _chemin_fichier(imp, source)

    try:
        entete = pd.read_csv(chemin, nrows=0, encoding="utf-8-sig")
    except Exception as exc:
        raise ExtractionError(f"Le fichier « {imp.nom_fichier} » n'a pas pu être lu : {exc}")
    colonnes_fichier = list(entete.columns)
    nom_table = imp.nom_fichier or source.nom
    attendues = _colonnes_csv_retenues(selection, colonnes_fichier, imp.nom_fichier)

    try:
        df = pd.read_csv(chemin, usecols=attendues, encoding="utf-8-sig", nrows=LIMITE_LIGNES + 1)
    except Exception as exc:
        raise ExtractionError(f"Le fichier « {imp.nom_fichier} » n'a pas pu être lu : {exc}")

    return {nom_table: df[attendues]}


# ──────────────────────────────────────────────────────────────────────────────
# Extraction — script SQL importé
# ──────────────────────────────────────────────────────────────────────────────

_ECHAPPEMENTS = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "'": "'", '"': '"'}


def _lire_chaine(texte: str, i: int) -> tuple[str, int]:
    """Lit une chaîne SQL quotée. `i` pointe sur l'apostrophe ouvrante."""
    i += 1
    morceaux: list[str] = []
    n = len(texte)
    while i < n:
        c = texte[i]
        if c == "\\" and i + 1 < n:
            morceaux.append(_ECHAPPEMENTS.get(texte[i + 1], texte[i + 1]))
            i += 2
            continue
        if c == "'":
            if i + 1 < n and texte[i + 1] == "'":  # '' = apostrophe échappée
                morceaux.append("'")
                i += 2
                continue
            return "".join(morceaux), i + 1
        morceaux.append(c)
        i += 1
    return "".join(morceaux), i


def _convertir_token(token: str):
    token = token.strip()
    if not token or token.upper() == "NULL":
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token.strip("`\"")


def _lire_tuple(texte: str, i: int) -> tuple[list, int]:
    """Lit un tuple de VALUES. `i` pointe juste après la parenthèse ouvrante."""
    valeurs: list = []
    n = len(texte)
    while i < n:
        while i < n and texte[i] in " \t\r\n":
            i += 1
        if i < n and texte[i] == ")":
            return valeurs, i + 1
        if i < n and texte[i] == "'":
            valeur, i = _lire_chaine(texte, i)
        else:
            debut = i
            while i < n and texte[i] not in ",)":
                if texte[i] == "'":
                    _, i = _lire_chaine(texte, i)
                    continue
                i += 1
            valeur = _convertir_token(texte[debut:i])
        valeurs.append(valeur)
        while i < n and texte[i] in " \t\r\n":
            i += 1
        if i < n and texte[i] == ",":
            i += 1
        elif i < n and texte[i] == ")":
            return valeurs, i + 1
    return valeurs, i


def _colonnes_create_table(sql_text: str, table: str) -> list[str]:
    """Colonnes déclarées par le CREATE TABLE de `table` (même logique qu'à l'upload)."""
    motif = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?" + re.escape(table) + r"[`\"\]]?\s*\(",
        re.IGNORECASE,
    )
    m = motif.search(sql_text)
    if not m:
        return []
    profondeur, debut, pos = 1, m.end(), m.end()
    while pos < len(sql_text) and profondeur > 0:
        if sql_text[pos] == "(":
            profondeur += 1
        elif sql_text[pos] == ")":
            profondeur -= 1
        pos += 1
    corps = sql_text[debut : pos - 1]
    mots_cles = {"primary", "foreign", "key", "constraint", "unique", "index", "check"}
    colonnes = []
    for ligne in corps.split(","):
        m_col = re.match(r"[`\"\[]?(\w+)[`\"\]]?\s+\w", ligne.strip())
        if m_col and m_col.group(1).lower() not in mots_cles:
            colonnes.append(m_col.group(1))
    return colonnes


def _lignes_insert(sql_text: str, table: str, colonnes_create: list[str]) -> tuple[list[str], list[list]]:
    """Colonnes et lignes issues des INSERT INTO `table` du script."""
    motif = re.compile(
        r"INSERT\s+(?:LOW_PRIORITY\s+|DELAYED\s+|HIGH_PRIORITY\s+|IGNORE\s+)*INTO\s+"
        r"[`\"\[]?" + re.escape(table) + r"[`\"\]]?\s*(\(([^)]*)\))?\s*VALUES\s*",
        re.IGNORECASE,
    )
    colonnes: list[str] = []
    lignes: list[list] = []
    for m in motif.finditer(sql_text):
        if m.group(2):
            cols = [c.strip().strip("`\"[]") for c in m.group(2).split(",")]
        else:
            cols = list(colonnes_create)
        if not colonnes:
            colonnes = cols
        i = m.end()
        n = len(sql_text)
        while i < n:
            while i < n and sql_text[i] in " \t\r\n":
                i += 1
            if i >= n or sql_text[i] != "(":
                break
            valeurs, i = _lire_tuple(sql_text, i + 1)
            if len(valeurs) == len(cols):
                lignes.append(valeurs)
            while i < n and sql_text[i] in " \t\r\n":
                i += 1
            if i < n and sql_text[i] == ",":
                i += 1
                continue
            break
    return colonnes, lignes


def _tables_du_fichier_sql(sql_text: str) -> list[str]:
    return re.findall(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s*\(",
        sql_text,
        re.IGNORECASE,
    )


def _table_sql_retenue(nom_table: str, tables_fichier: list[str], source: SourceDonnee, imp: ImportDonnee) -> str:
    """Résout la clé de sélection en table réelle du fichier."""
    if nom_table in tables_fichier:
        return nom_table
    # Repli du wizard : quand aucune table n'était détectée à l'upload, la clé enregistrée
    # vaut le nom de la source (item « Fichier SQL complet »). Ce repli reste volontairement
    # étroit — sinon un nom de table erroné passerait en silence.
    if nom_table in {source.nom, imp.nom_fichier} and len(tables_fichier) == 1:
        return tables_fichier[0]
    raise ExtractionError(
        f"La table « {nom_table} » est absente du fichier « {imp.nom_fichier} »."
        + (f" Tables disponibles : {', '.join(tables_fichier)}." if tables_fichier else "")
    )


def _extraire_sql(imp: ImportDonnee, source: SourceDonnee, selection: dict) -> dict[str, pd.DataFrame]:
    """Rejoue les INSERT du script importé, en ne conservant que les colonnes retenues.

    Le script est une source autonome : ses tables sont celles déclarées *dans le fichier*,
    sans aucun rapport avec la base de l'entreprise. Un fichier multi-tables est donc
    analysable table par table.
    """
    chemin = _chemin_fichier(imp, source)

    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
            sql_text = fh.read()
    except Exception as exc:
        raise ExtractionError(f"Le fichier « {imp.nom_fichier} » n'a pas pu être lu : {exc}")

    tables_fichier = _tables_du_fichier_sql(sql_text)

    resultats: dict[str, pd.DataFrame] = {}
    for nom_table, colonnes_voulues in selection.items():
        table = _table_sql_retenue(nom_table, tables_fichier, source, imp)

        colonnes_create = _colonnes_create_table(sql_text, table)
        colonnes_insert, lignes = _lignes_insert(sql_text, table, colonnes_create)
        if not colonnes_insert:
            colonnes_insert = colonnes_create
        if not colonnes_insert:
            raise ExtractionError(
                f"Aucune colonne n'a pu être identifiée pour « {table} » dans le fichier « {imp.nom_fichier} »."
            )

        attendues = colonnes_insert if _est_selection_totale(colonnes_voulues) else list(colonnes_voulues)
        manquantes = [c for c in attendues if c not in colonnes_insert]
        if manquantes:
            raise ExtractionError(
                f"Colonnes absentes de « {table} » dans le fichier « {imp.nom_fichier} » : "
                f"{', '.join(manquantes)}."
            )

        # Ne matérialiser que les colonnes retenues.
        index_gardes = [colonnes_insert.index(c) for c in attendues]
        donnees = [[ligne[i] for i in index_gardes] for ligne in lignes[: LIMITE_LIGNES + 1]]
        resultats[table] = pd.DataFrame(donnees, columns=attendues)

    return resultats


# ──────────────────────────────────────────────────────────────────────────────
# Validation et typage
# ──────────────────────────────────────────────────────────────────────────────

def _est_selection_totale(valeur) -> bool:
    """`"*"` (ou toute valeur non-liste) = table/fichier entier sélectionné."""
    return not isinstance(valeur, list)


def _normaliser_types(df: pd.DataFrame, nom_table: str) -> tuple[pd.DataFrame, list[str]]:
    """Rend les colonnes exploitables par 4.2 : numérique et date quand c'est possible,
    texte sinon. Ne devine jamais partiellement (conversion tout-ou-rien par colonne)."""
    avertissements: list[str] = []
    df = df.copy()

    for colonne in df.columns:
        serie = df[colonne]
        if serie.isna().all():
            avertissements.append(f"« {nom_table}.{colonne} » est entièrement vide.")
            continue
        if pd.api.types.is_numeric_dtype(serie) or pd.api.types.is_datetime64_any_dtype(serie):
            continue
        if pd.api.types.is_bool_dtype(serie):
            continue

        non_nulles = serie.dropna()
        if non_nulles.empty:
            continue

        converti = pd.to_numeric(serie, errors="coerce")
        if converti.notna().sum() == non_nulles.shape[0]:
            df[colonne] = converti
            continue

        try:
            converti = pd.to_datetime(serie, errors="coerce", format="mixed")
        except Exception:
            converti = pd.Series([pd.NaT] * len(serie), index=serie.index)
        if converti.notna().sum() == non_nulles.shape[0]:
            df[colonne] = converti
            continue

        df[colonne] = serie.astype("string")

    return df, avertissements


def _valider(nom_table: str, df: pd.DataFrame, attendues: list[str] | None) -> list[str]:
    """Contrôles de validité — lève `ExtractionError` avec un message explicite."""
    if df.shape[1] == 0:
        raise ExtractionError(f"Aucune colonne n'a été extraite pour « {nom_table} ».")

    if attendues:
        manquantes = [c for c in attendues if c not in df.columns]
        if manquantes:
            raise ExtractionError(
                f"Colonnes attendues absentes des données extraites de « {nom_table} » : "
                f"{', '.join(manquantes)}."
            )

    if df.empty:
        raise ExtractionError(
            f"Aucune donnée exploitable dans « {nom_table} » : la sélection ne renvoie aucune ligne."
        )

    avertissements: list[str] = []
    if len(df) > LIMITE_LIGNES:
        avertissements.append(
            f"« {nom_table} » dépasse {LIMITE_LIGNES:,} lignes : l'analyse portera sur les "
            f"{LIMITE_LIGNES:,} premières.".replace(",", " ")
        )

    vides = [c for c in df.columns if df[c].isna().all()]
    if len(vides) == df.shape[1]:
        raise ExtractionError(
            f"Toutes les colonnes sélectionnées de « {nom_table} » sont vides : rien à analyser."
        )

    return avertissements


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────────────

def extraire_donnees(db, config) -> ResultatExtraction:
    """Extrait les données sélectionnées d'une `ConfigurationAnalyse`.

    Lève `ExtractionError` (message en français) pour tout échec métier.
    """
    try:
        facteurs = json.loads(config.facteurs_selectionnes or "{}")
    except Exception:
        raise ExtractionError("La sélection de données de cette configuration est illisible.")

    selection = facteurs.get("tables", {}) if isinstance(facteurs, dict) else {}
    if not selection:
        raise ExtractionError("Aucune donnée n'est sélectionnée dans cette configuration.")

    imp = db.query(ImportDonnee).filter(ImportDonnee.id_import == config.id_import).first()
    if not imp:
        raise ExtractionError("L'import lié à cette configuration est introuvable.")

    source = db.query(SourceDonnee).filter(SourceDonnee.id_source == imp.id_source).first()
    if not source:
        raise ExtractionError("La source liée à cette configuration est introuvable.")
    if source.statut == "suspendu":
        raise ExtractionError(f"La source « {source.nom} » a été suspendue par un administrateur.")

    # Branchement explicite : les trois types de sources sont de valeur égale, mais leur
    # sélection n'a pas la même structure et leur support n'est pas le même (fichier
    # autonome sur le serveur vs. base tierce interrogée en direct).
    if source.type_source == "BDD":
        brut = _extraire_bdd(db, source, selection)
    elif source.type_source == "CSV":
        brut = _extraire_csv(imp, source, selection)
    elif source.type_source == "SQL":
        brut = _extraire_sql(imp, source, selection)
    else:
        raise ExtractionError(f"Type de source non pris en charge : « {source.type_source} ».")

    donnees: dict[str, pd.DataFrame] = {}
    avertissements: list[str] = []
    for nom_table, df in brut.items():
        attendues = None
        valeur = selection.get(nom_table)
        if valeur is not None and not _est_selection_totale(valeur):
            attendues = list(valeur)

        avertissements += _valider(nom_table, df, attendues)
        df = df.head(LIMITE_LIGNES)
        df, avert_types = _normaliser_types(df, nom_table)
        avertissements += avert_types
        donnees[nom_table] = df

    return ResultatExtraction(
        config_id=config.id_configuration,
        source_id=source.id_source,
        source_nom=source.nom,
        source_type=source.type_source,
        donnees=donnees,
        avertissements=avertissements,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Profilage léger (wizard) — mêmes lecteurs que l'extraction, sur un échantillon
# ──────────────────────────────────────────────────────────────────────────────

ECHANTILLON_PROFIL = 200


def colonnes_temporelles(df: pd.DataFrame) -> list[str]:
    """Colonnes reconnues comme dates une fois `_normaliser_types` appliqué.

    Source **unique** de la détection temporelle : partagée par le profilage du wizard
    (compatibilité des objectifs) et par la préparation 4.2. Le verdict repose sur le dtype
    réel obtenu après conversion, jamais sur le nom de la colonne.
    """
    return [str(col) for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col])]


def _points_par_frequence(df_complet: pd.DataFrame, colonne_date: str | None) -> dict[str, int]:
    """Points obtenus à chaque pas de temps, pour guider le choix de fréquence.

    Calculé sur la colonne de date **entière** (et non l'échantillon de typage) : c'est un
    décompte, il doit être exact. Une seule colonne est lue, le coût reste faible.
    """
    from app.services.moteur_analyse.fiabilite import PAS_PAR_FREQUENCE, points_par_frequence

    if not colonne_date or colonne_date not in df_complet.columns:
        return {freq: 0 for freq in PAS_PAR_FREQUENCE}
    return points_par_frequence(df_complet[colonne_date])


def _decrire_colonnes(df: pd.DataFrame) -> list[dict]:
    """Description typée d'un échantillon, telle que la verra l'extraction réelle."""
    dates = set(colonnes_temporelles(df))
    return [
        {
            "nom": str(col),
            "type": str(df[col].dtype),
            "est_date": str(col) in dates,
            "est_numerique": bool(pd.api.types.is_numeric_dtype(df[col])),
        }
        for col in df.columns
    ]


def _profiler_bdd(db, source: SourceDonnee, selection: dict, echantillon: int) -> dict:
    from sqlalchemy import MetaData, Table, func as sa_func, inspect as sa_inspect, select

    engine = _ouvrir_connexion_source(db, source)
    profil: dict[str, dict] = {}
    try:
        try:
            tables_reelles = set(sa_inspect(engine).get_table_names())
        except Exception as exc:
            raise ExtractionError(f"Lecture impossible de la structure de « {source.nom} » : {exc}")

        for nom_table, colonnes in selection.items():
            if nom_table not in tables_reelles:
                raise ExtractionError(f"La table « {nom_table} » n'existe plus dans « {source.nom} ».")
            try:
                table = Table(nom_table, MetaData(), autoload_with=engine)
            except Exception as exc:
                raise ExtractionError(f"Lecture impossible de la table « {nom_table} » : {exc}")

            attendues = (
                list(table.columns.keys()) if _est_selection_totale(colonnes)
                else [c for c in colonnes if c in table.columns]
            )
            if not attendues:
                continue
            try:
                with engine.connect() as conn:
                    df = pd.read_sql(select(*[table.c[c] for c in attendues]).limit(echantillon), conn)
                    total = conn.execute(select(sa_func.count()).select_from(table)).scalar() or 0
            except Exception as exc:
                raise ExtractionError(f"Lecture impossible des données de « {nom_table} » : {exc}")

            df, _ = _normaliser_types(df, nom_table)
            dates = colonnes_temporelles(df)
            # Décompte exact des périodes : relit la seule colonne de date, pas la table.
            points_freq = {}
            if dates:
                try:
                    with engine.connect() as conn:
                        col = pd.read_sql(select(table.c[dates[0]]).limit(LIMITE_LIGNES), conn)
                    points_freq = _points_par_frequence(col, dates[0])
                except Exception:
                    points_freq = {}
            profil[nom_table] = {
                "lignes": int(total),
                "colonnes": _decrire_colonnes(df),
                "points_par_frequence": points_freq,
            }
    finally:
        engine.dispose()
    return profil


def _profiler_csv(imp: ImportDonnee, source: SourceDonnee, selection: dict, echantillon: int) -> dict:
    chemin = _chemin_fichier(imp, source)
    try:
        colonnes_fichier = list(pd.read_csv(chemin, nrows=0, encoding="utf-8-sig").columns)
    except Exception as exc:
        raise ExtractionError(f"Le fichier « {imp.nom_fichier} » n'a pas pu être lu : {exc}")

    attendues = _colonnes_csv_retenues(selection, colonnes_fichier, imp.nom_fichier)
    nom_table = imp.nom_fichier or source.nom
    try:
        df = pd.read_csv(chemin, usecols=attendues, encoding="utf-8-sig", nrows=echantillon)
        # Comptage par blocs sur une seule colonne : correct vis-à-vis des sauts de ligne
        # échappés, et sans charger le fichier entier en mémoire.
        total = sum(
            len(bloc) for bloc in pd.read_csv(
                chemin, usecols=[attendues[0]], encoding="utf-8-sig", chunksize=50_000
            )
        )
    except Exception as exc:
        raise ExtractionError(f"Le fichier « {imp.nom_fichier} » n'a pas pu être lu : {exc}")

    df, _ = _normaliser_types(df[attendues], nom_table)
    dates = colonnes_temporelles(df)
    points_freq = {}
    if dates:
        try:
            colonne = pd.read_csv(chemin, usecols=[dates[0]], encoding="utf-8-sig", nrows=LIMITE_LIGNES)
            colonne, _ = _normaliser_types(colonne, nom_table)
            points_freq = _points_par_frequence(colonne, dates[0])
        except Exception:
            points_freq = {}
    return {nom_table: {
        "lignes": int(total),
        "colonnes": _decrire_colonnes(df),
        "points_par_frequence": points_freq,
    }}


def _profiler_sql(imp: ImportDonnee, source: SourceDonnee, selection: dict, echantillon: int) -> dict:
    chemin = _chemin_fichier(imp, source)
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
            sql_text = fh.read()
    except Exception as exc:
        raise ExtractionError(f"Le fichier « {imp.nom_fichier} » n'a pas pu être lu : {exc}")

    tables_fichier = _tables_du_fichier_sql(sql_text)
    profil: dict[str, dict] = {}
    for nom_table, colonnes_voulues in selection.items():
        table = _table_sql_retenue(nom_table, tables_fichier, source, imp)
        colonnes_create = _colonnes_create_table(sql_text, table)
        colonnes_insert, lignes = _lignes_insert(sql_text, table, colonnes_create)
        colonnes_insert = colonnes_insert or colonnes_create
        if not colonnes_insert:
            continue

        attendues = (
            colonnes_insert if _est_selection_totale(colonnes_voulues)
            else [c for c in colonnes_voulues if c in colonnes_insert]
        )
        if not attendues:
            continue
        index_gardes = [colonnes_insert.index(c) for c in attendues]
        echantillon_lignes = [[l[i] for i in index_gardes] for l in lignes[:echantillon]]
        df, _ = _normaliser_types(pd.DataFrame(echantillon_lignes, columns=attendues), table)

        # Le fichier est déjà parsé : le décompte exact ne coûte qu'une colonne de plus.
        dates = colonnes_temporelles(df)
        points_freq = {}
        if dates:
            i_date = colonnes_insert.index(dates[0])
            toutes, _ = _normaliser_types(
                pd.DataFrame([[l[i_date]] for l in lignes], columns=[dates[0]]), table
            )
            points_freq = _points_par_frequence(toutes, dates[0])

        profil[table] = {
            "lignes": len(lignes),
            "colonnes": _decrire_colonnes(df),
            "points_par_frequence": points_freq,
        }
    return profil


def profiler_selection(db, source: SourceDonnee, selection: dict, echantillon: int = ECHANTILLON_PROFIL) -> dict:
    """Profil léger des données sélectionnées, pour le guidage du wizard.

    Renvoie `{"<table>": {"lignes": n, "colonnes": [{nom, type, est_date, est_numerique}]}}`.
    Le typage passe par `_normaliser_types`, le même que l'extraction : ce que le wizard
    annonce est donc exactement ce que 4.1 produira. Seul un échantillon est lu pour le
    typage — le comptage de lignes reste exact.
    """
    if not selection:
        return {}
    if source.statut == "suspendu":
        raise ExtractionError(f"La source « {source.nom} » a été suspendue par un administrateur.")

    if source.type_source == "BDD":
        return _profiler_bdd(db, source, selection, echantillon)

    imp = db.query(ImportDonnee).filter(ImportDonnee.id_source == source.id_source).order_by(
        ImportDonnee.date_import.desc()
    ).first()
    if not imp:
        raise ExtractionError(f"Aucun import n'est rattaché à la source « {source.nom} ».")

    if source.type_source == "CSV":
        return _profiler_csv(imp, source, selection, echantillon)
    if source.type_source == "SQL":
        return _profiler_sql(imp, source, selection, echantillon)
    raise ExtractionError(f"Type de source non pris en charge : « {source.type_source} ».")


def executer_extraction(db, config) -> ResultatExtraction | None:
    """Enveloppe `extraire_donnees` avec le suivi d'état en base.

    Passe la configuration en « extraction en cours », puis enregistre le message d'échec
    et le statut d'erreur si l'extraction échoue. Retourne `None` en cas d'échec — le
    message est alors lisible dans `config.message_execution`.
    """
    from datetime import datetime

    config.statut_execution = STATUT_EXTRACTION_EN_COURS
    config.message_execution = None
    db.commit()

    try:
        resultat = extraire_donnees(db, config)
    except ExtractionError as exc:
        config.statut_execution = STATUT_ERREUR_DONNEES
        config.message_execution = exc.message_complet()
        db.commit()
        return None
    except Exception as exc:  # filet : aucune exception brute ne doit remonter
        config.statut_execution = STATUT_ERREUR_DONNEES
        config.message_execution = f"Erreur inattendue pendant l'extraction : {exc}"
        db.commit()
        return None

    total = sum(len(df) for df in resultat.donnees.values())
    message = f"Extraction réussie : {total} ligne(s) sur {len(resultat.donnees)} table(s)."
    if resultat.avertissements:
        message += " " + " ".join(resultat.avertissements)

    # 4.2 n'existe pas encore : la configuration retourne en attente du moteur.
    config.statut_execution = "en_attente"
    config.message_execution = message
    config.derniere_execution = datetime.utcnow()
    db.commit()
    return resultat
