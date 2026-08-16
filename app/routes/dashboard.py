import csv
import io
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.database.connection import get_db
from app.database.models.administrateur import Administrateur
from app.database.models.alerte import Alerte
from app.database.models.configuration_analyse import ConfigurationAnalyse
from app.database.models.connexion_bdd import ConnexionBDD
from app.database.models.entreprise import Entreprise, StatutDemandeEnum
from app.database.models.import_donnee import ImportDonnee
from app.database.models.parametre_systeme import ensure_defaults, get_all as get_params, set_param
from app.database.models.resultat_analyse import ResultatAnalyse
from app.database.models.source_donnee import SourceDonnee
from app.database.models.utilisateur import RoleEnum, StatutCompteEnum, Utilisateur
from app.dependencies import get_current_user, require_role
from app.services import entreprise_service, utilisateur_service
from app.services.moteur_analyse import (
    NIVEAUX_FIABILITE,
    OBJECTIFS_TEMPORELS,
    PAS_PAR_FREQUENCE,
    TYPE_PAR_OBJECTIF,
    COULEUR_PAR_NIVEAU,
    LIBELLE_PAR_NIVEAU,
    NIVEAUX_CRITICITE,
    SEUIL_FIABILITE_BONNE,
    adequation_frequence,
    STATUT_ERREUR_DONNEES,
    STATUT_EXTRACTION_EN_COURS,
    ExtractionError,
    SpecificationInvalide,
    executer_et_stocker,
    executer_extraction,
    executer_preparation,
    interpreter_et_stocker,
    LIBELLE_MODELE,
    decrire_colonnes,
    evaluer_fiabilite,
    intention_depuis_resume,
    profiler_selection,
    traduire_intention,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/


def _contexte_alertes(request: Request) -> dict:
    """Alimente la cloche du topbar sur **toutes** les pages, en un seul endroit.

    Passer par un context processor plutôt que par les ~15 routes : le badge doit être
    juste partout, et le recopier dans chaque `TemplateResponse` garantissait qu'une
    route oubliée afficherait un compteur faux.

    Les deux rôles ont une cloche, mais elle ne dit pas la même chose :
    - **entreprise** : ses alertes non traitées, chacune menant à son résultat ;
    - **administrateur** : ce qui appelle son attention sur le parc, chacun menant à
      l'écran où il agit. Jamais le détail métier d'une entreprise.

    Ne lève jamais : une cloche indisponible n'empêche aucune page de s'afficher.
    """
    vide = {"cloche_total": 0, "cloche_role": None,
            "alertes_recentes": [], "supervision": []}
    try:
        from app.core.security import decode_access_token
        from app.database.connection import SessionLocal
        from app.database.models.utilisateur import Utilisateur

        jeton = request.cookies.get("access_token")
        if not jeton:
            return vide
        charge = decode_access_token(jeton)
        if not charge or not charge.get("sub"):
            return vide

        db = SessionLocal()
        try:
            user = db.get(Utilisateur, int(charge["sub"]))
            if not user:
                return vide
            role = getattr(user.role, "value", user.role)

            if role == "entreprise":
                from app.services.alertes import alertes_recentes, compter_non_lues

                return {
                    "cloche_total": compter_non_lues(db, user.idUtilisateur),
                    "cloche_role": "entreprise",
                    "supervision": [],
                    "alertes_recentes": [
                        {
                            "id": a.id_alerte,
                            "niveau": a.niveau,
                            "message": a.message or "",
                            "type": a.type_alerte or "",
                            "id_resultat": a.id_resultat,
                            "id_configuration": a.id_configuration,
                            "date": a.date_creation.strftime("%d/%m à %H:%M") if a.date_creation else "",
                        }
                        for a in alertes_recentes(db, user.idUtilisateur)
                    ],
                }

            if role == "administrateur":
                from app.services.supervision import NATURES, notifications_admin

                items = notifications_admin(db)
                return {
                    "cloche_total": sum(i["nombre"] for i in items),
                    "cloche_role": "administrateur",
                    "alertes_recentes": [],
                    "supervision": [
                        {**i, "nature_libelle": NATURES[i["nature"]][0],
                         "ton": NATURES[i["nature"]][1]}
                        for i in items
                    ],
                }
            return vide
        finally:
            db.close()
    except Exception:
        return vide


templates = Jinja2Templates(
    directory=os.path.join(BASE_DIR, "templates"),
    context_processors=[_contexte_alertes],
)
templates.env.globals["asset_version"] = str(int(time.time()))

router = APIRouter(tags=["dashboard"])

# Traçage de la traduction de l'intention (Module 4). Visible dans la console uvicorn.
# Un échec de traduction ne doit jamais passer inaperçu : il est journalisé ici ET
# stocké dans ConfigurationAnalyse.intention_erreur.
_log = logging.getLogger("stockvision.moteur_analyse")

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")


def _build_db_url(type_sgbd: str, username: str, password: str, host: str, port: int, db_name: str) -> str:
    if type_sgbd == "mysql":
        return f"mysql+pymysql://{username}:{password}@{host}:{port}/{db_name}"
    if type_sgbd == "postgresql":
        return f"postgresql+psycopg2://{username}:{password}@{host}:{port}/{db_name}"
    return f"mssql+pyodbc://{username}:{password}@{host}:{port}/{db_name}?driver=ODBC+Driver+17+for+SQL+Server"


def _list_tables(url: str, type_sgbd: str, db_name: str) -> list[str]:
    from sqlalchemy import create_engine, text as sa_text

    engine_test = create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
    try:
        with engine_test.connect() as conn:
            if type_sgbd == "mysql":
                result = conn.execute(sa_text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :db ORDER BY table_name"
                ), {"db": db_name})
            elif type_sgbd == "postgresql":
                result = conn.execute(sa_text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                ))
            else:
                result = conn.execute(sa_text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_catalog = :db AND table_schema = 'dbo' ORDER BY table_name"
                ), {"db": db_name})
            tables = [row[0] for row in result]
    finally:
        engine_test.dispose()
    return tables


def _fmt_size(n) -> str:
    if not n:
        return "—"
    if n < 1024:
        return f"{n} o"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} Ko"
    return f"{n / (1024 * 1024):.1f} Mo"


def _build_facteurs_selectionnes(source_id: int, items: list[str]) -> dict:
    """Regroupe la sélection plate de l'étape 2 (clés "table" ou "table.colonne") en
    { "source_id": ..., "tables": { "<table>": ["col1", "col2"] | "*" } }
    "*" signifie que la table/le fichier entier a été sélectionné sans détail de colonnes."""
    tables: dict[str, object] = {}
    for key in items:
        if "." in key:
            table, column = key.split(".", 1)
            existing = tables.get(table)
            if existing == "*":
                continue  # la table entière est déjà sélectionnée, plus précis inutile
            tables.setdefault(table, [])
            tables[table].append(column)
        else:
            tables[key] = "*"
    return {"source_id": source_id, "tables": tables}


def _count_facteurs(facteurs_json: str) -> int:
    """Nombre d'éléments sélectionnés (colonnes précises, ou 1 par table sélectionnée entière)."""
    try:
        data = json.loads(facteurs_json)
    except Exception:
        return 0
    tables = data.get("tables", {}) if isinstance(data, dict) else {}
    return sum(len(cols) if isinstance(cols, list) else 1 for cols in tables.values())


def _flatten_facteurs(facteurs_json: str) -> list[str]:
    """Inverse de _build_facteurs_selectionnes : reconstruit la liste plate de clés
    ("table" pour une table entière, "table.colonne" pour une colonne précise)."""
    try:
        data = json.loads(facteurs_json)
    except Exception:
        return []
    tables = data.get("tables", {}) if isinstance(data, dict) else {}
    items = []
    for table, cols in tables.items():
        if cols == "*" or not isinstance(cols, list):
            items.append(table)
        else:
            items.extend(f"{table}.{c}" for c in cols)
    return items


def _collect_sources_data(db: Session, sources: list) -> list[dict]:
    """Construit les lignes d'historique d'import (fichier ou connexion BD) pour une liste de SourceDonnee."""
    connexions = {
        c.id_source: c
        for c in db.query(ConnexionBDD).filter(
            ConnexionBDD.id_source.in_([s.id_source for s in sources])
        ).all()
    } if sources else {}

    sources_data = []
    for src in sources:
        imports = (
            db.query(ImportDonnee)
            .filter(ImportDonnee.id_source == src.id_source)
            .order_by(ImportDonnee.date_import.desc())
            .all()
        )
        sync_count = len(imports)
        derniere_date = imports[0].date_import if imports else None

        if src.type_source == "BDD":
            connexion = connexions.get(src.id_source)
            tables = json.loads(connexion.tables_detectees) if connexion and connexion.tables_detectees else []
            row = {
                "id_source": src.id_source,
                "kind": "db",
                "nom": src.nom,
                "statut": src.statut,
                "type_sgbd": connexion.type_sgbd if connexion else "—",
                "host": connexion.host if connexion else "",
                "db_name": connexion.db_name if connexion else "",
                "table_count": len(tables),
                "connexion_id": connexion.id_connexion if connexion else None,
                "derniere_date": (connexion.date_derniere_sync if connexion and connexion.date_derniere_sync else derniere_date),
                "sync_count": sync_count,
            }
        else:
            dernier = imports[0] if imports else None
            meta = {}
            if dernier and dernier.meta_json:
                try:
                    meta = json.loads(dernier.meta_json)
                except Exception:
                    meta = {}
            row = {
                "id_source": src.id_source,
                "kind": "file",
                "nom": src.nom,
                "statut": dernier.statut if dernier else src.statut,
                "type_format": src.type_source,
                "nom_fichier": dernier.nom_fichier if dernier else src.nom,
                "import_id": dernier.id_import if dernier else None,
                "derniere_date": derniere_date,
                "sync_count": sync_count,
                "taille_octets": dernier.taille_octets if dernier else None,
                "columns": meta.get("columns", []),
                "sql_creates": meta.get("creates"),
                "sql_inserts": meta.get("inserts"),
            }

        row["date_creation"] = src.date_creation
        row["date_creation_fmt"] = src.date_creation.strftime("%d/%m/%Y") if src.date_creation else "—"
        row["derniere_date_fmt"] = row["derniere_date"].strftime("%d/%m/%Y %H:%M") if row["derniere_date"] else "—"
        if row["kind"] == "file":
            row["taille_fmt"] = _fmt_size(row.get("taille_octets"))
        sources_data.append(row)

    return sources_data

# (title, guide) pour chaque section admin accessible via le placeholder
ADMIN_SECTIONS: dict[str, tuple[str, str]] = {
    "imports":        ("Supervision des imports",         "Visualisez l'ensemble des données importées par les entreprises, tous secteurs confondus."),
    "configurations": ("Analyses en cours",               "Consultez les configurations d'analyse créées par les entreprises et leur état d'exécution."),
    "alertes":        ("Centre d'alertes",                "Retrouvez toutes les alertes critiques détectées sur l'ensemble des entreprises."),
    "parametres":     ("Configuration de la plateforme",  "Ajustez les réglages globaux d'analyse, de sécurité et de notifications de StockVision AI."),
}

# Rétro-compat : utilisé uniquement en fallback
SIDEBAR_TITLES = {k: v[0] for k, v in ADMIN_SECTIONS.items()}


def _pending_count(db: Session) -> int:
    return (
        db.query(Entreprise)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.en_attente)
        .count()
    )


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/dashboard/admin")
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    from app.services.alertes import NIVEAUX_ALERTE, STATUT_TRAITEE
    from app.services.graphiques import (
        alertes_ouvertes_par_jour,
        entreprises_actives,
        series_parc,
    )
    from app.services.supervision import (
        RANG_CRITIQUE,
        STATUTS_SOURCE_ERREUR,
        activite_par_entreprise,
        activite_recente,
    )

    pending_count = _pending_count(db)
    entreprises_count = (
        db.query(Entreprise).filter(Entreprise.statut_demande == StatutDemandeEnum.validee).count()
    )
    utilisateurs_count = db.query(Utilisateur).count()
    sources_erreur = (
        db.query(SourceDonnee).filter(SourceDonnee.statut.in_(STATUTS_SOURCE_ERREUR)).count()
    )
    alertes_critiques = (
        db.query(Alerte).filter(Alerte.criticite_rang >= RANG_CRITIQUE)
        .filter(Alerte.statut != STATUT_TRAITEE).count()
    )

    debut_mois = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    volumes = activite_par_entreprise(db, debut_mois)
    analyses_mois = sum(v["analyses_mois"] for c, v in volumes.items() if c != "_vide")

    series = series_parc(db)
    series["encours"] = alertes_ouvertes_par_jour(db)

    # Répartition par niveau : les teintes viennent de criticite.py, aucune créée.
    # Seuls les rangs ≥ « élevé » produisent une alerte, il n'y a donc que 2 parts.
    comptes = dict(
        db.query(Alerte.niveau, func.count(Alerte.id_alerte))
        .filter(Alerte.statut != STATUT_TRAITEE).group_by(Alerte.niveau).all()
    )
    total_alertes = sum(comptes.values()) or 1
    repartition_niveaux = [
        {
            "label": niv["libelle"],
            "color": COULEUR_PAR_NIVEAU.get(niv["code"], "#64748b"),
            "nombre": comptes.get(niv["code"], 0),
            "pct": round(comptes.get(niv["code"], 0) * 100 / total_alertes),
        }
        for niv in NIVEAUX_ALERTE
    ]

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_overview.html",
        {
            "user": user,
            "active_page": "overview",
            "pending_count": pending_count,
            "entreprises_count": entreprises_count,
            "utilisateurs_count": utilisateurs_count,
            "sources_erreur": sources_erreur,
            "alertes_critiques": alertes_critiques,
            "analyses_mois": analyses_mois,
            "series": series,
            "series_courbe": {c: series[c] for c in ("analyses", "imports", "inscriptions")},
            "repartition_niveaux": repartition_niveaux,
            "classement": entreprises_actives(db, volumes),
            # Le rail de triage vient du context processor (`supervision`) : il est
            # déjà servi à chaque page, inutile de le recalculer ici.
            "activite": [
                {**a, "periode": _periode_activite(a)} for a in activite_recente(db, limite=5)
            ],
            "notifications_count": pending_count,
        },
    )
    return _no_store(response)


@router.get("/dashboard/admin/demandes")
def admin_demandes(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    statut: str = "en_attente",
    q: str = "",
):
    demandes = entreprise_service.list_entreprises(db, statut=statut, q=q)
    pending_count = _pending_count(db)
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_demandes.html",
        {
            "user": user,
            "active_page": "demandes",
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "demandes": demandes,
            "statut_filtre": statut,
            "q": q,
        },
    )
    return _no_store(response)


# Au-delà, une entreprise est dite « dormante ». C'est un constat, jamais une alerte :
# ne pas utiliser la plateforme n'est pas un incident, et le liseré reste neutre.
DELAI_DORMANCE = timedelta(days=30)

# Une alerte non traitée depuis plus de deux semaines n'est plus un événement récent :
# c'est un dossier oublié. Le seuil qualifie l'ancienneté, il ne déclenche rien.
SEUIL_ALERTE_ANCIENNE = 14


def _fmt_date(valeur, avec_heure: bool = False) -> str:
    if not valeur:
        return "—"
    return valeur.strftime("%d/%m/%Y à %H:%M" if avec_heure else "%d/%m/%Y")


def _anciennete(valeur) -> str:
    """« il y a 3 jours » — un délai se lit plus vite qu'une date à soustraire."""
    if not valeur:
        return "Aucune activité"
    jours = (datetime.now() - valeur).days
    if jours <= 0:
        return "Aujourd'hui"
    if jours == 1:
        return "Hier"
    if jours < 31:
        return f"Il y a {jours} jours"
    if jours < 365:
        return f"Il y a {jours // 30} mois"
    return "Il y a plus d'un an"


def _est_dormante(valeur) -> bool:
    return valeur is not None and (datetime.now() - valeur) > DELAI_DORMANCE


@router.get("/dashboard/admin/entreprises")
def admin_entreprises(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    statut: str = "validee",
    q: str = "",
):
    from app.services.supervision import activite_par_entreprise

    entreprises = entreprise_service.list_entreprises(db, statut=statut, q=q)
    pending_count = _pending_count(db)

    debut_mois = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    volumes = activite_par_entreprise(db, debut_mois)
    vide = volumes.get("_vide", {})

    lignes = []
    for entreprise, utilisateur in entreprises:
        v = volumes.get(entreprise.idEntreprise, vide)
        derniere = v.get("derniere_activite")
        # Deux signalements de nature différente, à ne surtout pas confondre :
        # « attention » appelle un regard (une source cassée, une alerte critique non
        # traitée), « dormante » est un simple constat. Une entreprise qui n'utilise pas
        # la plateforme n'est pas un incident — elle ne porte donc aucun liseré.
        attention = bool(v.get("sources_erreur")) or bool(v.get("alertes_critiques"))
        lignes.append({
            "entreprise": entreprise,
            "nom": entreprise.nom,
            "initiale": (entreprise.nom or "?")[0].upper(),
            "secteur": entreprise.secteur_activite or "Non renseigné",
            "email": utilisateur.email,
            "statut": entreprise.statut_demande.value,
            "inscription": _fmt_date(entreprise.date_inscription),
            "derniere_activite": _fmt_date(derniere, avec_heure=True),
            "anciennete": _anciennete(derniere),
            "jamais_active": derniere is None,
            "dormante": _est_dormante(derniere),
            "attention": attention,
            "ton": "critique" if v.get("alertes_critiques") else ("eleve" if v.get("sources_erreur") else ""),
            **{c: v.get(c, 0) for c in ("sources", "sources_erreur", "analyses",
                                        "analyses_mois", "alertes", "alertes_critiques")},
        })

    reels = [v for cle, v in volumes.items() if cle != "_vide"]
    tuiles = {
        "actives": db.query(Entreprise).filter(
            Entreprise.statut_demande == StatutDemandeEnum.validee).count(),
        "actives_mois": sum(1 for v in reels
                            if v["derniere_activite"] and v["derniere_activite"] >= debut_mois),
        "sources_erreur": sum(v["sources_erreur"] for v in reels),
        "analyses_mois": sum(v["analyses_mois"] for v in reels),
    }

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_entreprises.html",
        {
            "user": user,
            "active_page": "entreprises",
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "entreprises": entreprises,
            "lignes": lignes,
            "tuiles": tuiles,
            "statut_filtre": statut,
            "q": q,
        },
    )
    return _no_store(response)


@router.get("/dashboard/admin/entreprises/export")
def admin_entreprises_export(
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    statut: str = "validee",
    q: str = "",
):
    rows = entreprise_service.list_entreprises(db, statut=statut, q=q)

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Entreprise", "Secteur", "Email", "Statut", "Date d'inscription"])
    for entreprise, utilisateur in rows:
        writer.writerow(
            [
                entreprise.nom,
                entreprise.secteur_activite or "",
                utilisateur.email,
                entreprise.statut_demande.value,
                entreprise.date_inscription.strftime("%d/%m/%Y")
                if entreprise.date_inscription
                else "",
            ]
        )

    # BOM pour qu'Excel ouvre correctement les accents.
    content = "﻿" + buffer.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="entreprises.csv"'},
    )


@router.get("/dashboard/admin/utilisateurs")
def admin_utilisateurs(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    role: str = "tous",
    q: str = "",
):
    utilisateurs = utilisateur_service.list_utilisateurs(db, role=role, q=q)
    pending_count = _pending_count(db)
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_utilisateurs.html",
        {
            "user": user,
            "active_page": "utilisateurs",
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "utilisateurs": utilisateurs,
            "compteurs": utilisateur_service.count_by_role(db),
            "role_filtre": role,
            "q": q,
        },
    )
    return _no_store(response)


@router.post("/dashboard/admin/utilisateurs/{id_utilisateur}/statut")
def admin_utilisateur_toggle(
    id_utilisateur: int,
    role: str = Form("tous"),
    q: str = Form(""),
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    utilisateur_service.toggle_statut(db, id_utilisateur, user.idUtilisateur)
    url = f"/dashboard/admin/utilisateurs?role={role}"
    if q:
        url += f"&q={q}"
    return RedirectResponse(url=url, status_code=303)


@router.get("/dashboard/admin/statistiques")
def admin_statistiques(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    pending_count = _pending_count(db)
    par_secteur = (
        db.query(Entreprise.secteur_activite, func.count(Entreprise.idEntreprise))
        .filter(Entreprise.statut_demande == StatutDemandeEnum.validee)
        .group_by(Entreprise.secteur_activite)
        .order_by(func.count(Entreprise.idEntreprise).desc())
        .all()
    )
    stats = {
        "en_attente": pending_count,
        "validees": db.query(Entreprise)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.validee)
        .count(),
        "refusees": db.query(Entreprise)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.refusee)
        .count(),
        "utilisateurs": db.query(Utilisateur).count(),
    }
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_statistiques.html",
        {
            "user": user,
            "active_page": "statistiques",
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "stats": stats,
            "par_secteur": par_secteur,
        },
    )
    return _no_store(response)


@router.post("/dashboard/admin/demandes/{id_entreprise}/approuver")
def admin_demande_approuver(
    id_entreprise: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    entreprise_service.approve_entreprise(db, id_entreprise, user.idUtilisateur)
    return RedirectResponse(url="/dashboard/admin/demandes?statut=validee", status_code=303)


@router.post("/dashboard/admin/demandes/{id_entreprise}/refuser")
def admin_demande_refuser(
    id_entreprise: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    entreprise_service.reject_entreprise(db, id_entreprise)
    return RedirectResponse(url="/dashboard/admin/demandes?statut=refusee", status_code=303)


@router.get("/dashboard/admin/parametres")
def admin_parametres(
    request: Request,
    tab: str = "general",
    flash_ok: str = "",
    flash_err: str = "",
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    if tab not in ("general", "notifications", "apparence"):
        tab = "general"

    ensure_defaults(db)
    params = get_params(db)

    # Stats plateforme
    entreprises_actives = (
        db.query(Entreprise)
        .join(Utilisateur, Entreprise.idEntreprise == Utilisateur.idUtilisateur)
        .filter(Utilisateur.statut_compte == StatutCompteEnum.actif)
        .count()
    )
    analyses_total = db.query(ResultatAnalyse).count()

    pending_count = _pending_count(db)
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_parametres.html",
        {
            "user": user,
            "active_page": "parametres",
            "active_tab": tab,
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "params": params,
            "smtp": {
                "host": settings.SMTP_HOST,
                "port": settings.SMTP_PORT,
                "from_addr": settings.SMTP_FROM,
            },
            "platform": {
                "entreprises_actives": entreprises_actives,
                "analyses_total": analyses_total,
            },
            "flash_ok": flash_ok,
            "flash_err": flash_err,
        },
    )
    return _no_store(response)


@router.post("/dashboard/admin/parametres")
def admin_parametres_save(
    request: Request,
    tab: str = "general",
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    # Onglet général
    frequence_analyse_defaut: str = Form(None),
    seuil_criticite_defaut: str   = Form(None),
    duree_reset_mdp_heures: str   = Form(None),
    duree_session_minutes: str    = Form(None),
    # Onglet notifications (checkboxes — absentes du POST si décochées)
    notif_nouvelle_demande: str   = Form(None),
    notif_securite: str           = Form(None),
    notif_resume_hebdo: str       = Form(None),
):
    try:
        if tab == "general":
            if frequence_analyse_defaut:
                set_param(db, "frequence_analyse_defaut", frequence_analyse_defaut)
            if seuil_criticite_defaut is not None:
                val = int(seuil_criticite_defaut)
                if not 0 <= val <= 100:
                    raise ValueError("Seuil hors plage (0-100)")
                set_param(db, "seuil_criticite_defaut", str(val))
            if duree_reset_mdp_heures is not None:
                val = int(duree_reset_mdp_heures)
                if not 1 <= val <= 72:
                    raise ValueError("Durée hors plage (1-72h)")
                set_param(db, "duree_reset_mdp_heures", str(val))
            if duree_session_minutes is not None:
                val = int(duree_session_minutes)
                if not 5 <= val <= 1440:
                    raise ValueError("Durée hors plage (5-1440 min)")
                set_param(db, "duree_session_minutes", str(val))

        elif tab == "notifications":
            set_param(db, "notif_nouvelle_demande", "1" if notif_nouvelle_demande == "1" else "0")
            set_param(db, "notif_securite",         "1" if notif_securite == "1" else "0")
            set_param(db, "notif_resume_hebdo",     "1" if notif_resume_hebdo == "1" else "0")

        db.commit()
        return RedirectResponse(
            url=f"/dashboard/admin/parametres?tab={tab}&flash_ok=Paramètres enregistrés avec succès.",
            status_code=303,
        )
    except (ValueError, Exception) as exc:
        db.rollback()
        return RedirectResponse(
            url=f"/dashboard/admin/parametres?tab={tab}&flash_err=Erreur : {exc}",
            status_code=303,
        )


@router.get("/dashboard/admin/imports")
def admin_imports_supervision(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    kind: str = "all",
    entreprise_id: int = 0,
    q: str = "",
):
    """Supervision globale des imports : toutes les SourceDonnee, toutes entreprises confondues."""
    query = (
        db.query(SourceDonnee, Entreprise)
        .join(Entreprise, SourceDonnee.idEntreprise == Entreprise.idEntreprise)
    )
    if entreprise_id:
        query = query.filter(SourceDonnee.idEntreprise == entreprise_id)
    if kind == "file":
        query = query.filter(SourceDonnee.type_source != "BDD")
    elif kind == "db":
        query = query.filter(SourceDonnee.type_source == "BDD")

    rows = query.order_by(SourceDonnee.id_source.desc()).all()
    sources = [src for src, _ in rows]
    entreprise_by_source = {src.id_source: ent for src, ent in rows}

    sources_data = _collect_sources_data(db, sources)
    for row in sources_data:
        ent = entreprise_by_source.get(row["id_source"])
        row["entreprise_nom"] = ent.nom if ent else "—"
        row["entreprise_id"] = ent.idEntreprise if ent else None

    if q:
        q_lower = q.strip().lower()
        sources_data = [
            r for r in sources_data
            if q_lower in (r["nom"] or "").lower() or q_lower in (r["entreprise_nom"] or "").lower()
        ]

    total_imports = len(sources_data)
    actives_count = sum(1 for r in sources_data if r["statut"] in ("actif", "valide"))
    erreurs_count = sum(1 for r in sources_data if r["statut"] == "erreur")

    entreprises = (
        db.query(Entreprise)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.validee)
        .order_by(Entreprise.nom)
        .all()
    )

    pending_count = _pending_count(db)
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_imports.html",
        {
            "user": user,
            "active_page": "imports",
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "title": "Supervision des imports",
            "guide": "Visualisez l'ensemble des données importées par les entreprises, tous secteurs confondus.",
            "sources": sources_data,
            "entreprises": entreprises,
            "kind_filtre": kind,
            "entreprise_id_filtre": entreprise_id,
            "q": q,
            "total_imports": total_imports,
            "actives_count": actives_count,
            "erreurs_count": erreurs_count,
        },
    )
    return _no_store(response)


@router.post("/dashboard/admin/imports/{source_id}/suspend")
async def admin_imports_suspend(
    source_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    """Suspend (ou réactive) une source de données pour bloquer son usage dans de nouvelles configurations d'analyse."""
    source = db.query(SourceDonnee).filter(SourceDonnee.id_source == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")

    if source.statut == "suspendu":
        source.statut = "actif"
        suspended = False
    else:
        source.statut = "suspendu"
        suspended = True

    db.commit()

    return JSONResponse({"ok": True, "suspended": suspended, "statut": source.statut})


@router.post("/dashboard/admin/parametres/test-smtp")
async def admin_parametres_test_smtp(
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    from app.utils.email import send_email
    try:
        await send_email(
            to=settings.SMTP_FROM or settings.SMTP_USER,
            subject="[StockVision AI] Email de test",
            body=(
                "Bonjour,\n\n"
                "Ceci est un email de test envoyé depuis les paramètres de la plateforme StockVision AI.\n"
                "Si vous recevez ce message, la configuration SMTP est correcte.\n\n"
                "— StockVision AI"
            ),
        )
        return JSONResponse({"message": "Email de test envoyé avec succès."})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Échec SMTP : {exc}")


def _periode_activite(groupe: dict) -> str:
    """Instant ou plage horaire d'un groupe d'actions.

    Une plage n'est affichée que si elle en est une : sur la même minute, « de 09:14 à
    09:14 » serait une lourdeur pour rien.
    """
    debut, fin = groupe.get("debut"), groupe.get("fin")
    if not fin:
        return "—"
    if not debut or debut.strftime("%d/%m %H:%M") == fin.strftime("%d/%m %H:%M"):
        return fin.strftime("%d/%m à %H:%M")
    if debut.date() == fin.date():
        return f"{fin.strftime('%d/%m')} de {debut.strftime('%H:%M')} à {fin.strftime('%H:%M')}"
    return f"du {debut.strftime('%d/%m à %H:%M')} au {fin.strftime('%d/%m à %H:%M')}"


@router.get("/dashboard/admin/alertes")
def admin_alertes(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    """Supervision : volumes d'alertes par entreprise et par niveau.

    ⚠️ **Aucun message, aucun motif, aucun lien vers un résultat.** L'administrateur
    surveille la charge d'alertes du parc ; le détail métier appartient à l'entreprise,
    et il n'agit pas à sa place.
    """
    from app.services.alertes import NIVEAUX_ALERTE, synthese_admin
    from app.services.graphiques import alertes_ouvertes_par_jour, series_parc
    from app.services.supervision import activite_recente

    title, guide = ADMIN_SECTIONS["alertes"]
    lignes = synthese_admin(db)
    totaux = {
        "entreprises": len(lignes),
        "total": sum(l["total"] for l in lignes),
        "non_traitees": sum(l["non_traitees"] for l in lignes),
        **{niv["code"]: sum(l.get(niv["code"], 0) for l in lignes) for niv in NIVEAUX_ALERTE},
    }
    # Part de chaque niveau dans le total : c'est la ligne de contexte des tuiles.
    totaux["parts"] = {
        niv["code"]: round(totaux[niv["code"]] * 100 / totaux["total"]) if totaux["total"] else 0
        for niv in NIVEAUX_ALERTE
    }
    # Part de chaque niveau, pour la barre de répartition. Calculée ici : un gabarit ne
    # doit pas faire d'arithmétique.
    for ligne in lignes:
        ligne["parts"] = {
            niv["code"]: round(ligne.get(niv["code"], 0) * 100 / ligne["total"], 1)
            if ligne["total"] else 0
            for niv in NIVEAUX_ALERTE
        }

    # Ancienneté du plus vieux dossier ouvert du parc. C'est LE chiffre qui distingue
    # « des alertes arrivent » de « des alertes s'accumulent sans être traitées ».
    ouvertes = [l for l in lignes if l["anciennete_jours"] is not None]
    plus_vieille = max((l["anciennete_jours"] for l in ouvertes), default=None)
    totaux["anciennete"] = plus_vieille
    totaux["anciennete_libelle"] = (
        "aucune alerte ouverte" if plus_vieille is None
        else "ouverte aujourd'hui" if plus_vieille == 0
        else f"ouverte depuis {plus_vieille} jour" + ("s" if plus_vieille > 1 else "")
    )
    # Au-delà de ce délai, une alerte non traitée cesse d'être un événement récent.
    totaux["anciennete_ton"] = ("critique" if (plus_vieille or 0) >= SEUIL_ALERTE_ANCIENNE
                                else "eleve" if (plus_vieille or 0) >= 7 else "")

    for ligne in lignes:
        ligne["anciennete_texte"] = _anciennete(ligne["plus_ancienne"])
        ligne["delai_texte"] = (
            f"traitées en {ligne['delai_moyen_h']} h en moyenne"
            if ligne["delai_moyen_h"] is not None
            # Sans alerte traitée, il n'existe aucun délai moyen : on montre à la
            # place l'âge du plus vieux dossier ouvert, jamais une moyenne inventée.
            else "aucune alerte encore traitée"
        )

    # Séries des 30 derniers jours : mêmes producteurs que le tableau de bord.
    series = {
        "encours": alertes_ouvertes_par_jour(db),
        "declenchees": series_parc(db)["alertes"],
    }

    pending_count = db.query(Entreprise).filter(
        Entreprise.statut_demande == StatutDemandeEnum.en_attente).count()

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_alertes.html",
        {
            "user": user, "active_page": "alertes",
            "pending_count": pending_count, "notifications_count": pending_count,
            "title": title, "guide": guide,
            "lignes": lignes, "totaux": totaux, "niveaux": NIVEAUX_ALERTE,
            "series": series,
            "activite": [
                {**a, "periode": _periode_activite(a)} for a in activite_recente(db, limite=5)
            ],
        },
    )
    return _no_store(response)


@router.get("/dashboard/admin/configurations")
def admin_configurations(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    entreprise_id: str = "",
    statut: str = "tous",
    frequence: str = "toutes",
):
    """Supervision des configurations d'analyse du parc.

    ⚠️ **Déclarée AVANT `/dashboard/admin/{section}`** : FastAPI apparie dans
    l'ordre de déclaration, la route générique l'absorberait sinon.
    """
    from app.services.supervision_analyses import (
        FREQUENCES,
        STATUTS_EXECUTION,
        historique_executions,
        indicateurs_parc,
        lister_configurations,
    )

    # ── Contrôle de saisie ───────────────────────────────────────────────────
    # Tout paramètre d'URL est une saisie : il est confronté aux valeurs connues
    # avant d'atteindre la couche de données. Une valeur inconnue retombe sur le
    # défaut plutôt que de vider silencieusement la liste ou de lever une erreur.
    statuts_valides = {code for code, _, _ in STATUTS_EXECUTION} | {"tous"}
    frequences_valides = {code for code, _ in FREQUENCES} | {"toutes"}
    statut = statut if statut in statuts_valides else "tous"
    frequence = frequence if frequence in frequences_valides else "toutes"

    entreprises = (
        db.query(Entreprise)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.validee)
        .order_by(Entreprise.nom).all()
    )
    ids_connus = {e.idEntreprise for e in entreprises}
    # `entreprise_id` arrive en texte : on ne le convertit que s'il désigne bien
    # une entreprise existante. Un identifiant fabriqué ne filtre rien.
    filtre_entreprise = None
    if entreprise_id.strip().isdigit() and int(entreprise_id) in ids_connus:
        filtre_entreprise = int(entreprise_id)

    debut_mois = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    indicateurs = indicateurs_parc(db, debut_mois)

    # Les comptes du filtre sont calculés AVANT filtrage : un filtre doit annoncer
    # ce qu'il cache.
    toutes = lister_configurations(db, objectif_pour_admin, filtre_entreprise)
    comptes_statut = {code: 0 for code, _, _ in STATUTS_EXECUTION}
    comptes_frequence = {code: 0 for code, _ in FREQUENCES}
    for l in toutes:
        comptes_statut[l["etat"]] = comptes_statut.get(l["etat"], 0) + 1
        comptes_frequence[l["frequence"]] = comptes_frequence.get(l["frequence"], 0) + 1

    lignes = [l for l in toutes
              if (statut == "tous" or l["etat"] == statut)
              and (frequence == "toutes" or l["frequence"] == frequence)]

    # L'historique est chargé pour les seules configurations listées, en une passe.
    historiques = {l["id"]: historique_executions(db, l["id"]) for l in lignes}

    title, guide = ADMIN_SECTIONS["configurations"]
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_configurations.html",
        {
            "user": user,
            "active_page": "configurations",
            "pending_count": _pending_count(db),
            "notifications_count": _pending_count(db),
            "title": title,
            "guide": guide,
            "indicateurs": indicateurs,
            "lignes": [
                {**l,
                 "derniere_fmt": _fmt_date(l["derniere"], avec_heure=True),
                 "prochaine_fmt": _fmt_date(l["prochaine"], avec_heure=True),
                 "creee_fmt": _fmt_date(l["creee"]),
                 "historique": historiques.get(l["id"], [])}
                for l in lignes
            ],
            "entreprises": entreprises,
            "statuts": STATUTS_EXECUTION,
            "frequences": FREQUENCES,
            "comptes_statut": comptes_statut,
            "comptes_frequence": comptes_frequence,
            "total": len(toutes),
            "entreprise_filtre": filtre_entreprise or "",
            "statut_filtre": statut,
            "frequence_filtre": frequence,
        },
    )
    return _no_store(response)


@router.get("/dashboard/admin/{section}")
def admin_placeholder(
    section: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    title, guide = ADMIN_SECTIONS.get(
        section,
        (section.replace("-", " ").title(), "Cette section arrive bientôt.")
    )
    pending_count = _pending_count(db)
    response = templates.TemplateResponse(
        request,
        "pages/dashboard/placeholder.html",
        {
            "user": user,
            "active_page": section,
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "title": title,
            "guide": guide,
        },
    )
    return _no_store(response)


# (title, guide) pour chaque section entreprise
_ENTREPRISE_SECTIONS: dict[str, tuple[str, str]] = {
    "import":         ("Ajouter vos données",                  "Déposez vos fichiers CSV/SQL ou connectez votre base de données pour commencer une nouvelle analyse."),
    "imports":        ("Vos imports précédents",               "Retrouvez l'ensemble des données que vous avez importées, avec leur statut de validation."),
    "configurations": ("Paramétrer une analyse",               "Choisissez les données à analyser, définissez votre objectif et la fréquence d'exécution souhaitée."),
    "resultats":      ("Vos résultats d'analyse",              "Consultez les tendances, indicateurs et prévisions générés par l'intelligence artificielle à partir de vos données."),
    "alertes":        ("Alertes détectées",                    "Soyez informé rapidement de toute anomalie ou situation critique identifiée dans vos données."),
    "powerbi":        ("Vos tableaux de bord Power BI",        "Explorez vos données sous forme de graphiques et rapports interactifs."),
}


@router.get("/dashboard/entreprise")
def entreprise_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Page d'accueil entreprise : où en est sa chaîne, et quoi faire ensuite."""
    from app.services.alertes import alertes_recentes
    from app.services.graphiques import (
        alertes_ouvertes_par_jour,
        repartition_objectifs,
        series_entreprise,
    )
    from app.services.supervision import activite_par_entreprise
    from app.services.tableau_bord import etat_pipeline

    ident = user.idUtilisateur
    debut_mois = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Même producteur de volumes que la supervision admin : les deux consoles
    # lisent les mêmes chiffres, obtenus par les mêmes jointures.
    volumes_tous = activite_par_entreprise(db, debut_mois, id_entreprise=ident)
    volumes = volumes_tous.get(ident, volumes_tous.get("_vide", {}))
    pipeline = etat_pipeline(db, ident, volumes)

    derniers_resultats = []
    for ligne, config in (
        db.query(ResultatAnalyse, ConfigurationAnalyse)
        .join(ConfigurationAnalyse,
              ResultatAnalyse.id_configuration == ConfigurationAnalyse.id_configuration)
        .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .filter(SourceDonnee.idEntreprise == ident)
        .order_by(ResultatAnalyse.date_execution.desc())
        .limit(4).all()
    ):
        presentation = _presenter_objectif(config)
        derniers_resultats.append({
            "id_configuration": config.id_configuration,
            # Source unique de cet affichage : une configuration se reconnaît à
            # l'identique ici et sur la page des résultats.
            "objectif": presentation["label"],
            # Icône ET couleur viennent du même endroit que dans le wizard et la
            # liste des configurations : une analyse se reconnaît à l'identique.
            "icone": presentation["icon"],
            "couleur": presentation["color"],
            "modele": LIBELLE_MODELE.get(ligne.modele_applique, ligne.modele_applique or "—"),
            "criticite": ligne.criticite or "normal",
            "criticite_libelle": LIBELLE_PAR_NIVEAU.get(ligne.criticite, "Non évalué"),
            "criticite_motif": ligne.criticite_motif or "",
            "fiabilite": ligne.fiabilite or "—",
            "date": _fmt_date(ligne.date_execution, avec_heure=True),
        })

    dernieres_alertes = [
        {
            "id_configuration": a.id_configuration,
            "message": a.message,
            "niveau": a.niveau,
            "date": _fmt_date(a.date_creation, avec_heure=True),
        }
        for a in alertes_recentes(db, ident, limite=4)
    ]

    # Séries des 30 derniers jours. L'encours d'alertes est un ÉTAT reconstitué
    # depuis `date_traitement` : compter les créations donnerait une courbe qui ne
    # redescend jamais, alors que l'encours, lui, redescend.
    series = series_entreprise(db, ident)
    series["encours"] = alertes_ouvertes_par_jour(db, ident)

    derniere = volumes.get("derniere_activite")
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/entreprise_overview.html",
        {
            "user": user,
            "active_page": "overview",
            "notifications_count": 0,
            "pipeline": pipeline,
            "volumes": volumes,
            "series": series,
            "series_courbe": {c: series[c] for c in ("analyses", "imports", "alertes")},
            "repartition": repartition_objectifs(db, ident, _presenter_objectif),
            "derniere_activite": _anciennete(derniere),
            "activite_exacte": _fmt_date(derniere, avec_heure=True) if derniere
                               else "aucun import ni analyse",
            "derniers_resultats": derniers_resultats,
            "dernieres_alertes": dernieres_alertes,
        },
    )
    return _no_store(response)


@router.get("/dashboard/entreprise/import")
def entreprise_import_page(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    # 3 derniers imports de cette entreprise
    recent_imports = (
        db.query(ImportDonnee, SourceDonnee)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .filter(SourceDonnee.idEntreprise == user.idUtilisateur)
        .order_by(ImportDonnee.date_import.desc())
        .limit(5)
        .all()
    )
    imports_data = [
        {
            "nom_fichier": imp.nom_fichier,
            "type_format": imp.type_format,
            "statut": imp.statut,
            "nom_source": src.nom,
            "date_import": imp.date_import.strftime("%d/%m/%Y %H:%M") if imp.date_import else "—",
        }
        for imp, src in recent_imports
    ]
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/import.html",
        {
            "user": user,
            "active_page": "import",
            "notifications_count": 0,
            "pending_count": 0,
            "recent_imports": imports_data,
        },
    )
    return _no_store(response)


_ALLOWED_EXTENSIONS = {"csv", "sql"}
_MAX_SIZE = 50 * 1024 * 1024  # 50 Mo


@router.post("/dashboard/entreprise/import")
async def entreprise_import_upload(
    request: Request,
    file: UploadFile = File(...),
    nom_source: str = Form(""),
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    # Validation extension
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Format non supporté : seuls {', '.join(e.upper() for e in _ALLOWED_EXTENSIONS)} sont acceptés.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Le fichier est vide ou illisible.")
    if len(content) > _MAX_SIZE:
        raise HTTPException(status_code=422, detail="Fichier trop volumineux — 50 Mo maximum.")

    # Créer SourceDonnee + ImportDonnee (statut "en_attente" jusqu'à confirmation)
    source_nom = nom_source.strip() or filename
    source = SourceDonnee(
        type_source=ext.upper(),
        nom=source_nom[:150],
        statut="en_attente",
        idEntreprise=user.idUtilisateur,
    )
    db.add(source)
    db.flush()

    # Stocker le fichier sur disque (uploads/entreprise_{id}/) pour connaître sa taille réelle
    entreprise_dir = os.path.join(UPLOAD_DIR, f"entreprise_{user.idUtilisateur}")
    os.makedirs(entreprise_dir, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}_{filename}"
    stored_path = os.path.join(entreprise_dir, stored_filename)
    with open(stored_path, "wb") as f_out:
        f_out.write(content)
    taille_octets = os.path.getsize(stored_path)
    chemin_relatif = os.path.join(f"entreprise_{user.idUtilisateur}", stored_filename)

    # Aperçu CSV : 5 premières lignes + structure de colonnes
    preview = None
    row_count = 0
    col_count = 0
    columns_detected = []

    if ext == "csv":
        try:
            text_content = content.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text_content))
            rows = []
            for i, row in enumerate(reader):
                if i == 0:
                    col_count = len(row)
                    columns_detected = list(row.keys())
                if i < 5:
                    rows.append(dict(row))
                row_count += 1
            col_count = col_count or (len(rows[0]) if rows else 0)
            preview = rows
        except Exception:
            pass
        meta = {"columns": columns_detected}
    else:  # sql
        try:
            sql_text = content.decode("utf-8", errors="replace")
            n_creates = len(re.findall(r"\bCREATE\s+TABLE\b", sql_text, re.IGNORECASE))
            n_inserts = len(re.findall(r"\bINSERT\s+INTO\b", sql_text, re.IGNORECASE))
            tables_found = []
            columns_by_table = {}
            for m in re.finditer(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s*\(",
                sql_text, re.IGNORECASE,
            ):
                table_name = m.group(1)
                tables_found.append(table_name)
                # Extraire le corps parenthésé (jusqu'à la parenthèse fermante correspondante)
                depth = 1
                start = m.end()
                pos = start
                while pos < len(sql_text) and depth > 0:
                    if sql_text[pos] == "(":
                        depth += 1
                    elif sql_text[pos] == ")":
                        depth -= 1
                    pos += 1
                body = sql_text[start:pos - 1]
                cols = []
                sql_keywords = {"primary", "foreign", "key", "constraint", "unique", "index", "check"}
                for line in body.split(","):
                    line = line.strip()
                    col_match = re.match(r"[`\"\[]?(\w+)[`\"\]]?\s+\w", line)
                    if col_match and col_match.group(1).lower() not in sql_keywords:
                        cols.append(col_match.group(1))
                if cols:
                    columns_by_table[table_name] = cols
        except Exception:
            n_creates = n_inserts = 0
            tables_found = []
            columns_by_table = {}
        meta = {"creates": n_creates, "inserts": n_inserts, "tables": tables_found, "columns_by_table": columns_by_table}

    import_obj = ImportDonnee(
        nom_fichier=filename,
        type_format=ext.upper(),
        statut="en_attente",
        id_source=source.id_source,
        taille_octets=taille_octets,
        meta_json=json.dumps(meta),
        chemin_fichier=chemin_relatif,
    )
    db.add(import_obj)
    db.commit()
    db.refresh(import_obj)

    # Date de l'import pour le récap côté JS
    import_date = import_obj.date_import.strftime("%d/%m/%Y à %H:%M") if import_obj.date_import else ""

    return JSONResponse({
        "import_id":   import_obj.id_import,
        "preview":     preview,
        "row_count":   row_count,
        "col_count":   col_count,
        "file_size":   taille_octets,
        "import_date": import_date,
        "nom_source":  source_nom,
    })


@router.get("/dashboard/entreprise/import/recent")
async def entreprise_import_recent(
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    rows = (
        db.query(ImportDonnee, SourceDonnee)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .filter(SourceDonnee.idEntreprise == user.idUtilisateur)
        .order_by(ImportDonnee.date_import.desc())
        .limit(3)
        .all()
    )
    return JSONResponse({
        "imports": [
            {
                "nom_fichier":  imp.nom_fichier,
                "type_format":  imp.type_format,
                "statut":       imp.statut,
                "date_import":  imp.date_import.strftime("%d/%m/%Y %H:%M") if imp.date_import else "—",
            }
            for imp, src in rows
        ]
    })


@router.post("/dashboard/entreprise/import/connexion-db/test")
async def entreprise_db_test(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Tente une connexion vers une base tierce et liste ses tables (lecture seule)."""
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine, text

    body = await request.json()
    type_sgbd = body.get("type_sgbd", "").lower()
    host      = body.get("host", "").strip()
    port      = int(body.get("port", 0))
    db_name   = body.get("db_name", "").strip()
    username  = body.get("username", "").strip()
    password  = body.get("password", "")

    if type_sgbd not in ("mysql", "postgresql", "mssql"):
        raise HTTPException(status_code=422, detail="Type SGBD non supporté.")
    if not host or not db_name or not username:
        raise HTTPException(status_code=422, detail="Paramètres de connexion incomplets.")

    # Construire l'URL selon le SGBD
    if type_sgbd == "mysql":
        url = f"mysql+pymysql://{username}:{password}@{host}:{port or 3306}/{db_name}"
    elif type_sgbd == "postgresql":
        url = f"postgresql+psycopg2://{username}:{password}@{host}:{port or 5432}/{db_name}"
    else:  # mssql
        url = f"mssql+pyodbc://{username}:{password}@{host}:{port or 1433}/{db_name}?driver=ODBC+Driver+17+for+SQL+Server"

    try:
        engine_test = create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
        with engine_test.connect() as conn:
            # Vérifier que c'est bien en lecture seule (pas de SUPER ou CREATE privs)
            if type_sgbd == "mysql":
                result = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :db ORDER BY table_name"
                ), {"db": db_name})
            elif type_sgbd == "postgresql":
                result = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                ))
            else:
                result = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_catalog = :db AND table_schema = 'dbo' ORDER BY table_name"
                ), {"db": db_name})
            tables = [row[0] for row in result]
        engine_test.dispose()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)

    return JSONResponse({"ok": True, "tables": tables, "table_count": len(tables)})


@router.post("/dashboard/entreprise/import/connexion-db")
async def entreprise_db_save(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Chiffre les identifiants et enregistre la connexion BD tierce."""
    from cryptography.fernet import Fernet

    body = await request.json()
    type_sgbd = body.get("type_sgbd", "").lower()
    host      = body.get("host", "").strip()
    port      = int(body.get("port", 0) or 0)
    db_name   = body.get("db_name", "").strip()
    username  = body.get("username", "").strip()
    password  = body.get("password", "")
    tables    = body.get("tables", [])
    nom_source = body.get("nom_source", db_name or host).strip()[:150]

    if type_sgbd not in ("mysql", "postgresql", "mssql"):
        raise HTTPException(status_code=422, detail="Type SGBD non supporté.")

    fernet_key = settings.DB_ENCRYPTION_KEY
    if not fernet_key:
        raise HTTPException(status_code=500, detail="Clé de chiffrement non configurée.")

    f = Fernet(fernet_key.encode())
    user_chiffre     = f.encrypt(username.encode()).decode()
    password_chiffre = f.encrypt(password.encode()).decode()

    # Créer la SourceDonnee
    source = SourceDonnee(
        type_source="BDD",
        nom=nom_source,
        statut="actif",
        idEntreprise=user.idUtilisateur,
    )
    db.add(source)
    db.flush()

    # Créer ou mettre à jour ConnexionBDD
    connexion = ConnexionBDD(
        id_source=source.id_source,
        type_sgbd=type_sgbd,
        host=host,
        port=port or (3306 if type_sgbd == "mysql" else 5432 if type_sgbd == "postgresql" else 1433),
        db_name=db_name,
        user_chiffre=user_chiffre,
        password_chiffre=password_chiffre,
        tables_detectees=json.dumps(tables),
        date_derniere_sync=datetime.utcnow(),
    )
    db.add(connexion)

    # Log de la synchronisation initiale (comptabilisée comme un ImportDonnee)
    db.add(ImportDonnee(
        nom_fichier=nom_source,
        type_format="BDD",
        statut="valide",
        id_source=source.id_source,
    ))

    db.commit()
    db.refresh(connexion)

    return JSONResponse({
        "ok": True,
        "connexion_id": connexion.id_connexion,
        "source_id": source.id_source,
        "nom_source": nom_source,
        "table_count": len(tables),
    })


@router.post("/dashboard/entreprise/import/connexion-db/{connexion_id}/sync")
async def entreprise_db_sync(
    connexion_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Reconnecte et rafraîchit la liste des tables d'une connexion BD enregistrée."""
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine, text

    connexion = db.query(ConnexionBDD).filter(ConnexionBDD.id_connexion == connexion_id).first()
    if not connexion:
        raise HTTPException(status_code=404, detail="Connexion introuvable.")

    # Vérifier appartenance
    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == connexion.id_source,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    def _sync_count() -> int:
        return db.query(ImportDonnee).filter(ImportDonnee.id_source == source.id_source).count()

    fernet_key = settings.DB_ENCRYPTION_KEY
    f = Fernet(fernet_key.encode())
    username = f.decrypt(connexion.user_chiffre.encode()).decode()
    password = f.decrypt(connexion.password_chiffre.encode()).decode()

    type_sgbd = connexion.type_sgbd
    if type_sgbd == "mysql":
        url = f"mysql+pymysql://{username}:{password}@{connexion.host}:{connexion.port}/{connexion.db_name}"
    elif type_sgbd == "postgresql":
        url = f"postgresql+psycopg2://{username}:{password}@{connexion.host}:{connexion.port}/{connexion.db_name}"
    else:
        url = f"mssql+pyodbc://{username}:{password}@{connexion.host}:{connexion.port}/{connexion.db_name}?driver=ODBC+Driver+17+for+SQL+Server"

    try:
        engine_test = create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
        with engine_test.connect() as conn:
            if type_sgbd == "mysql":
                result = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :db ORDER BY table_name"
                ), {"db": connexion.db_name})
            elif type_sgbd == "postgresql":
                result = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                ))
            else:
                result = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_catalog = :db AND table_schema = 'dbo' ORDER BY table_name"
                ), {"db": connexion.db_name})
            tables = [row[0] for row in result]
        engine_test.dispose()
    except Exception as exc:
        source.statut = "erreur"
        db.commit()
        return JSONResponse({
            "ok": False,
            "error": str(exc),
            "sync_count": _sync_count(),
        }, status_code=200)

    connexion.tables_detectees = json.dumps(tables)
    connexion.date_derniere_sync = datetime.utcnow()
    source.statut = "actif"
    db.add(ImportDonnee(
        nom_fichier=source.nom,
        type_format="BDD",
        statut="valide",
        id_source=source.id_source,
    ))
    db.commit()

    return JSONResponse({
        "ok": True,
        "tables": tables,
        "table_count": len(tables),
        "date_derniere_sync": connexion.date_derniere_sync.strftime("%d/%m/%Y %H:%M"),
        "sync_count": _sync_count(),
    })


@router.get("/dashboard/entreprise/import/connexion-db/{source_id}/edit")
async def entreprise_db_edit_info(
    source_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Renvoie les infos d'une connexion BD pour préremplir le formulaire d'édition (jamais le mot de passe)."""
    from cryptography.fernet import Fernet

    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")

    connexion = db.query(ConnexionBDD).filter(ConnexionBDD.id_source == source_id).first()
    if not connexion:
        raise HTTPException(status_code=404, detail="Connexion introuvable.")

    fernet_key = settings.DB_ENCRYPTION_KEY
    f = Fernet(fernet_key.encode())
    username = f.decrypt(connexion.user_chiffre.encode()).decode()

    return JSONResponse({
        "source_id": source.id_source,
        "nom_source": source.nom,
        "type_sgbd": connexion.type_sgbd,
        "host": connexion.host,
        "port": connexion.port,
        "db_name": connexion.db_name,
        "username": username,
    })


@router.post("/dashboard/entreprise/import/connexion-db/{source_id}/update")
async def entreprise_db_update(
    source_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Met à jour une connexion BD existante. Le mot de passe n'est re-chiffré que s'il est fourni."""
    from cryptography.fernet import Fernet

    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")

    connexion = db.query(ConnexionBDD).filter(ConnexionBDD.id_source == source_id).first()
    if not connexion:
        raise HTTPException(status_code=404, detail="Connexion introuvable.")

    body = await request.json()
    type_sgbd = body.get("type_sgbd", "").lower()
    host      = body.get("host", "").strip()
    port      = int(body.get("port", 0) or 0)
    db_name   = body.get("db_name", "").strip()
    username  = body.get("username", "").strip()
    password  = body.get("password", "")
    nom_source = body.get("nom_source", db_name or host).strip()[:150]

    if type_sgbd not in ("mysql", "postgresql", "mssql"):
        raise HTTPException(status_code=422, detail="Type SGBD non supporté.")
    if not host or not db_name or not username:
        raise HTTPException(status_code=422, detail="Paramètres de connexion incomplets.")

    fernet_key = settings.DB_ENCRYPTION_KEY
    f = Fernet(fernet_key.encode())

    # Mot de passe : réutiliser l'ancien si laissé vide, sinon re-chiffrer le nouveau
    if password:
        password_plain = password
        password_chiffre = f.encrypt(password.encode()).decode()
    else:
        password_plain = f.decrypt(connexion.password_chiffre.encode()).decode()
        password_chiffre = connexion.password_chiffre

    port = port or (3306 if type_sgbd == "mysql" else 5432 if type_sgbd == "postgresql" else 1433)
    url = _build_db_url(type_sgbd, username, password_plain, host, port, db_name)

    def _sync_count() -> int:
        return db.query(ImportDonnee).filter(ImportDonnee.id_source == source.id_source).count()

    try:
        tables = _list_tables(url, type_sgbd, db_name)
    except Exception as exc:
        source.statut = "erreur"
        db.commit()
        return JSONResponse({"ok": False, "error": str(exc), "sync_count": _sync_count()}, status_code=200)

    connexion.type_sgbd = type_sgbd
    connexion.host = host
    connexion.port = port
    connexion.db_name = db_name
    connexion.user_chiffre = f.encrypt(username.encode()).decode()
    connexion.password_chiffre = password_chiffre
    connexion.tables_detectees = json.dumps(tables)
    connexion.date_derniere_sync = datetime.utcnow()
    source.nom = nom_source
    source.statut = "actif"

    db.add(ImportDonnee(
        nom_fichier=nom_source,
        type_format="BDD",
        statut="valide",
        id_source=source.id_source,
    ))
    db.commit()

    return JSONResponse({
        "ok": True,
        "source_id": source.id_source,
        "nom_source": nom_source,
        "table_count": len(tables),
        "tables": tables,
        "date_derniere_sync": connexion.date_derniere_sync.strftime("%d/%m/%Y %H:%M"),
        "sync_count": _sync_count(),
    })


@router.post("/dashboard/entreprise/import/confirm")
async def entreprise_import_confirm(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    body = await request.json()
    import_id = body.get("import_id")
    if not import_id:
        raise HTTPException(status_code=422, detail="import_id manquant.")

    import_obj = db.query(ImportDonnee).filter(ImportDonnee.id_import == import_id).first()
    if not import_obj:
        raise HTTPException(status_code=404, detail="Import introuvable.")

    # Vérifier que la source appartient à l'utilisateur
    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == import_obj.id_source,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    import_obj.statut = "valide"
    source.statut = "actif"
    db.commit()

    return JSONResponse({"row_count": None})


@router.get("/dashboard/entreprise/imports/{source_id}/apercu")
def entreprise_apercu_import(
    source_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Aperçu des premières lignes d'un import — pour l'entreprise propriétaire.

    Ce sont **ses** données : elle a le droit de les consulter pour vérifier que
    l'import s'est bien déroulé avant de configurer une analyse dessus.

    ⚠️ **L'appartenance est vérifiée ici, pas dans le service.** Le filtre sur
    `idEntreprise` fait partie de la requête : une source qui n'appartient pas au
    demandeur est introuvable, elle n'est pas « trouvée puis refusée ».
    """
    from app.services.moteur_analyse.extraction import ExtractionError, apercu_import

    source = (
        db.query(SourceDonnee)
        .filter(SourceDonnee.id_source == source_id)
        .filter(SourceDonnee.idEntreprise == user.idUtilisateur)
        .first()
    )
    if not source:
        return JSONResponse({"ok": False, "message": "Source introuvable."}, status_code=404)

    imp = (
        db.query(ImportDonnee)
        .filter(ImportDonnee.id_source == source.id_source)
        .order_by(ImportDonnee.date_import.desc())
        .first()
    )
    if not imp:
        return JSONResponse(
            {"ok": False, "message": "Aucun import n'est rattaché à cette source."},
            status_code=404,
        )

    try:
        apercu = apercu_import(db, imp, source)
    except ExtractionError as exc:
        # Message destiné à l'entreprise : c'est sa source, elle doit savoir quoi faire.
        return JSONResponse({"ok": False, "message": exc.message_complet()}, status_code=200)
    except Exception:
        _log.exception("[apercu] source %s illisible", source_id)
        return JSONResponse(
            {"ok": False, "message": "L'aperçu n'a pas pu être produit."}, status_code=200
        )
    return JSONResponse({"ok": True, **apercu})


@router.get("/dashboard/entreprise/imports")
def entreprise_imports_history(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Historique des imports : une carte par SourceDonnee (fichier ou connexion BD)."""
    sources = (
        db.query(SourceDonnee)
        .filter(SourceDonnee.idEntreprise == user.idUtilisateur)
        .order_by(SourceDonnee.id_source.desc())
        .all()
    )

    sources_data = _collect_sources_data(db, sources)

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/imports_history.html",
        {
            "user": user,
            "active_page": "imports",
            "notifications_count": 0,
            "pending_count": 0,
            "title": "Vos imports précédents",
            "guide": "Retrouvez l'ensemble des données que vous avez importées, avec leur statut de validation.",
            "sources": sources_data,
        },
    )
    return _no_store(response)


@router.post("/dashboard/entreprise/import/source/{source_id}/delete")
async def entreprise_source_delete(
    source_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Supprime une source de données (fichier ou connexion BD) et son historique."""
    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")

    imports = db.query(ImportDonnee).filter(ImportDonnee.id_source == source.id_source).all()
    for imp in imports:
        if imp.chemin_fichier:
            full_path = os.path.join(UPLOAD_DIR, imp.chemin_fichier)
            if os.path.isfile(full_path):
                try:
                    os.remove(full_path)
                except OSError:
                    pass

    db.query(ImportDonnee).filter(ImportDonnee.id_source == source.id_source).delete()
    db.query(ConnexionBDD).filter(ConnexionBDD.id_source == source.id_source).delete()
    db.delete(source)
    db.commit()

    return JSONResponse({"ok": True})


# `temporel` n'est pas redéfini ici : il dérive d'OBJECTIFS_TEMPORELS
# (app/services/moteur_analyse/preparation.py), et les niveaux de fiabilité de
# fiabilite.py — le wizard annonce donc exactement ce que la préparation 4.2 appliquera.
# La recommandation n'est PAS figée ici : elle est calculée à partir du profil réel des
# données sélectionnées (voir _evaluer_objectifs).
_OBJECTIFS = [
    {
        "val": "prevision_tendance",
        "label": "Détecter une tendance",
        "desc": "Repérez la direction générale que prennent vos données au fil du temps.",
        "icon": "trending-up",
        "color": "#1D9E75",
    },
    {
        "val": "prevision_evolution",
        "label": "Prévoir une évolution",
        "desc": "Anticipez les valeurs futures à partir de l'historique de vos données.",
        "icon": "chart-line",
        "color": "#378ADD",
    },
    {
        "val": "detection_anomalie",
        "label": "Identifier une anomalie",
        "desc": "Repérez les valeurs ou comportements inhabituels dès qu'ils apparaissent.",
        "icon": "alert-triangle",
        "color": "#D85A30",
    },
    {
        "val": "comparaison_classement",
        "label": "Comparer et classer",
        "desc": "Identifiez ce qui performe le mieux ou le moins bien parmi vos produits, clients ou régions.",
        "icon": "arrows-sort",
        "color": "#7F77DD",
    },
]
# Dérivé, jamais recopié : la liste des objectifs temporels vit dans 4.2. Il n'y a plus de
# min_points — le volume ne rend jamais un objectif indisponible, il qualifie sa fiabilité.
for _o in _OBJECTIFS:
    _o["temporel"] = _o["val"] in OBJECTIFS_TEMPORELS

# Ce qui bloque un objectif, et **comment le débloquer** : une carte indisponible qui se
# contente de nommer la contrainte laisse l'entreprise sans action. Chaque entrée est
# (constat, action) — deux champs distincts, pas une phrase à découper à l'affichage.
_BLOCAGES: dict[str, tuple[str, str]] = {
    "sans_date": (
        "Vos données ne contiennent aucune colonne de date.",
        "Choisissez un import qui porte une date (commande, facture, mouvement de stock), "
        "ou ajoutez la colonne de date à votre sélection à l'étape précédente.",
    ),
    "sans_mesure": (
        "Vos données portent une date, mais aucune valeur chiffrée à suivre dans le temps.",
        "Ajoutez à votre sélection une colonne chiffrée : quantité, montant, total.",
    ),
    # Distincts du précédent : ici il *y a* des colonnes chiffrées, mais aucune n'est une
    # grandeur. Sans cette nuance, le message passerait pour une erreur de détection —
    # et le constat nomme la bonne catégorie plutôt que de les mélanger.
    "sans_mesure_identifiants": (
        "Les seules colonnes chiffrées de votre sélection sont des identifiants "
        "(clés, numéros de ligne) : ce ne sont pas des grandeurs à analyser.",
        "Ajoutez une colonne qui mesure quelque chose : quantité, montant, total, durée.",
    ),
    "sans_mesure_booleens": (
        "Les seules colonnes chiffrées de votre sélection sont des indicateurs oui/non : "
        "ils décrivent un état, ils ne mesurent aucune quantité.",
        "Ajoutez une colonne qui mesure quelque chose : quantité, montant, total, durée. "
        "L'indicateur reste utilisable pour répartir les résultats.",
    ),
    "sans_mesure_non_mesurables": (
        "Les seules colonnes chiffrées de votre sélection sont des identifiants et des "
        "indicateurs oui/non : aucune ne mesure de quantité.",
        "Ajoutez une colonne qui mesure quelque chose : quantité, montant, total, durée.",
    ),
}

# Constat/action à retenir quand aucune grandeur ne subsiste, selon ce qui a réellement
# été écarté. Nommer la mauvaise catégorie ferait douter l'entreprise de la détection.
_CLES_SANS_MESURE = {
    (True, False): "sans_mesure_identifiants",
    (False, True): "sans_mesure_booleens",
    (True, True): "sans_mesure_non_mesurables",
    (False, False): "sans_mesure",
    "sans_donnee": (
        "La sélection ne contient aucune ligne exploitable.",
        "Vérifiez votre sélection à l'étape précédente, ou réimportez le fichier source.",
    ),
}

_OBJECTIF_LABELS = {o["val"]: o["label"] for o in _OBJECTIFS}


def _presenter_objectif(c) -> dict:
    """Comment identifier une configuration — **source unique** de cet affichage.

    Utilisée par la liste des configurations, son modal, ses filtres et la page de
    résultats : une configuration doit se reconnaître à l'identique partout.

    Une configuration peut n'avoir qu'un besoin exprimé (pas d'objectif prédéfini) :
    afficher une case vide la rendrait impossible à reconnaître. On retombe alors sur
    le besoin lui-même, avec l'icône déduite du type d'analyse réellement traduit.
    """
    besoin = (c.besoin or "").strip()
    if c.objectif:
        meta = _OBJECTIF_BY_VAL.get(c.objectif, _OBJECTIF_FALLBACK)
        return {
            "label": _OBJECTIF_LABELS.get(c.objectif, c.objectif),
            "sous_ligne": besoin,          # le besoin précise l'objectif, en second plan
            "titre": besoin,
            "icon": meta["icon"],
            "color": meta["color"],
            "libre": False,
        }
    if besoin:
        meta = _OBJECTIF_FALLBACK
        # L'icône vient du type d'analyse retenu par la traduction, s'il existe.
        try:
            spec = json.loads(c.specification_json or "{}")
            type_analyse = (spec.get("specification") or {}).get("type_analyse")
            cle = _OBJECTIF_PAR_TYPE.get(type_analyse)
            if cle:
                meta = _OBJECTIF_BY_VAL.get(cle, _OBJECTIF_FALLBACK)
        except Exception:
            pass
        return {
            "label": besoin,
            "sous_ligne": "",
            "titre": besoin,
            "icon": meta["icon"],
            "color": meta["color"],
            "libre": True,
        }
    return {
        "label": "Sans objectif ni besoin",
        "sous_ligne": "",
        "titre": "",
        "icon": _OBJECTIF_FALLBACK["icon"],
        "color": _OBJECTIF_FALLBACK["color"],
        "libre": True,
    }
_OBJECTIF_KEYS = {o["val"] for o in _OBJECTIFS}
_OBJECTIF_BY_VAL = {o["val"]: o for o in _OBJECTIFS}
# Objectifs obsolètes (dont l'ancien « optimisation_ressource », remplacé par
# « comparaison_classement ») : rendus en gris neutre plutôt que de casser l'affichage.
_OBJECTIF_FALLBACK = {"icon": "arrows-sort", "color": "#64748b"}

# Type d'analyse traduit → objectif équivalent, pour donner à une configuration « besoin
# libre » l'icône et la couleur de l'analyse réellement retenue. Dérivé, jamais recopié.
_OBJECTIF_PAR_TYPE = {type_: val for val, type_ in TYPE_PAR_OBJECTIF.items()}

# Catégorie de filtre pour les configurations sans objectif prédéfini. Sans elle, elles
# disparaîtraient silencieusement de la liste dès qu'un filtre d'objectif est actif.
_FILTRE_BESOIN_LIBRE = "besoin_libre"

# Mention affichée à l'administrateur pour une configuration sans objectif prédéfini.
# Elle NOMME la catégorie sans jamais citer le besoin.
MENTION_BESOIN_LIBRE = "Besoin exprimé librement"


def objectif_pour_admin(c) -> dict:
    """Vue d'un objectif **destinée à l'administrateur** — liste blanche stricte.

    ⚠️ `_presenter_objectif` ne convient pas côté admin : elle place le **besoin
    exprimé par l'entreprise** dans `sous_ligne` et dans `titre` (l'infobulle),
    y compris pour une configuration qui porte pourtant un objectif prédéfini.
    L'employer telle quelle exposerait un contenu métier sur une page de
    supervision.

    Comme `charge_utile()` en 4.5, cette fonction **énumère ce qui sort** plutôt
    que d'exclure ce qui ne doit pas sortir : le jour où `_presenter_objectif`
    gagne un champ, il ne franchira pas cette frontière tout seul.
    """
    vue = _presenter_objectif(c)
    return {
        "libelle": MENTION_BESOIN_LIBRE if vue["libre"] else vue["label"],
        "icone": vue["icon"],
        "couleur": vue["color"],
        "libre": vue["libre"],
    }

_FREQUENCE_COLORS = {
    "ponctuelle": "#1D9E75",
    "quotidienne": "#378ADD",
    "hebdomadaire": "#BA7517",
    "mensuelle": "#7F77DD",
}
_FREQUENCES = [
    ("ponctuelle", "Ponctuelle"),
    ("quotidienne", "Quotidienne"),
    ("hebdomadaire", "Hebdomadaire"),
    ("mensuelle", "Mensuelle"),
]


@router.get("/dashboard/entreprise/configurations")
def entreprise_configurations_list(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
    frequence: str = "toutes",
    objectif: str = "tous",
):
    """Liste des configurations d'analyse finalisées + point d'entrée vers l'assistant de création."""
    source_ids = [
        r[0] for r in db.query(SourceDonnee.id_source)
        .filter(SourceDonnee.idEntreprise == user.idUtilisateur).all()
    ]
    configs = []
    has_any_configs = False
    if source_ids:
        import_ids = [
            r[0] for r in db.query(ImportDonnee.id_import)
            .filter(ImportDonnee.id_source.in_(source_ids)).all()
        ]
        if import_ids:
            base_filters = (
                ConfigurationAnalyse.id_import.in_(import_ids),
                ConfigurationAnalyse.statut == "actif",
            )
            has_any_configs = db.query(ConfigurationAnalyse).filter(*base_filters).first() is not None

            query = db.query(ConfigurationAnalyse).filter(*base_filters)
            if frequence != "toutes" and frequence in {f[0] for f in _FREQUENCES}:
                query = query.filter(ConfigurationAnalyse.frequence == frequence)
            if objectif == _FILTRE_BESOIN_LIBRE:
                # Configurations pilotées par le seul besoin exprimé : sans cette branche
                # elles n'auraient correspondu à aucun filtre et auraient disparu.
                query = query.filter(
                    or_(ConfigurationAnalyse.objectif.is_(None), ConfigurationAnalyse.objectif == "")
                )
            elif objectif != "tous" and objectif in _OBJECTIF_KEYS:
                query = query.filter(ConfigurationAnalyse.objectif == objectif)
            configs = query.order_by(ConfigurationAnalyse.date_creation.desc()).all()
            _reparer_executions_bloquees(db, configs)

    # Nom de la source de chaque config (via ImportDonnee -> SourceDonnee), pour le détail
    source_noms = {}
    if configs:
        import_id_to_source_nom = {
            r[0]: r[1] for r in db.query(ImportDonnee.id_import, SourceDonnee.nom)
            .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
            .filter(ImportDonnee.id_import.in_([c.id_import for c in configs])).all()
        }
        source_noms = import_id_to_source_nom

    _STATUT_LABELS = {"actif": "Active", "brouillon": "Brouillon"}

    # Libellés dérivés de NIVEAUX_FIABILITE : aucune chaîne recopiée ici.
    _FIABILITE_LABELS = {code: label for _, code, label, _ in NIVEAUX_FIABILITE}

    configs_data = []
    for c in configs:
        presentation = _presenter_objectif(c)
        objectif_label = presentation["label"]
        frequence_label = dict(_FREQUENCES).get(c.frequence, c.frequence or "—")
        date_fmt = c.date_creation.strftime("%d/%m/%Y") if c.date_creation else "—"

        facteurs = {}
        if c.facteurs_selectionnes:
            try:
                facteurs = json.loads(c.facteurs_selectionnes).get("tables", {})
            except Exception:
                facteurs = {}

        configs_data.append({
            "id_configuration": c.id_configuration,
            "objectif": objectif_label,
            "objectif_sous_ligne": presentation["sous_ligne"],
            "objectif_titre": presentation["titre"],
            "objectif_libre": presentation["libre"],
            "objectif_icon": presentation["icon"],
            "objectif_color": presentation["color"],
            "frequence_val": c.frequence,
            "frequence": frequence_label,
            "frequence_color": _FREQUENCE_COLORS.get(c.frequence, "#64748b"),
            "date_creation": date_fmt,
            "nb_facteurs": _count_facteurs(c.facteurs_selectionnes) if c.facteurs_selectionnes else 0,
            "statut_execution": c.statut_execution,
            "prochaine_execution": c.prochaine_execution.strftime("%d/%m/%Y %H:%M") if c.prochaine_execution else None,
            "message_execution": c.message_execution,
            "fiabilite": _FIABILITE_LABELS.get(c.fiabilite_execution),
            "fiabilite_code": c.fiabilite_execution,
            "points_execution": c.points_execution,
            "detail": {
                "source_nom": source_noms.get(c.id_import, "—"),
                "objectif": objectif_label,
                "objectif_libre": presentation["libre"],
                "frequence": frequence_label,
                "besoin": c.besoin or "",
                "date_creation": date_fmt,
                "statut": _STATUT_LABELS.get(c.statut, c.statut),
                "tables": facteurs,
                "statut_execution": c.statut_execution,
                "derniere_execution": c.derniere_execution.strftime("%d/%m/%Y %H:%M") if c.derniere_execution else None,
                "prochaine_execution": c.prochaine_execution.strftime("%d/%m/%Y %H:%M") if c.prochaine_execution else None,
                "message_execution": c.message_execution,
                "intention_reformulee": c.intention_reformulee,
                "intention_erreur": c.intention_erreur,
                "fiabilite": _FIABILITE_LABELS.get(c.fiabilite_execution),
                "fiabilite_code": c.fiabilite_execution,
                "points_execution": c.points_execution,
            },
        })

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/configurations_list.html",
        {
            "user": user,
            "active_page": "configurations",
            "notifications_count": 0,
            "pending_count": 0,
            "title": "Vos configurations d'analyse",
            "guide": "Choisissez les données à analyser, définissez votre objectif et la fréquence d'exécution souhaitée.",
            "configs": configs_data,
            "has_any_configs": has_any_configs,
            "frequences": _FREQUENCES,
            "frequence_colors": _FREQUENCE_COLORS,
            "objectifs": _OBJECTIFS,
            "filtre_besoin_libre": _FILTRE_BESOIN_LIBRE,
            "frequence_filtre": frequence,
            "objectif_filtre": objectif,
        },
    )
    return _no_store(response)


@router.get("/dashboard/entreprise/configurations/new")
def entreprise_configuration_wizard(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Assistant de création de configuration d'analyse — étape 1 : choix de la source."""
    sources = (
        db.query(SourceDonnee)
        .filter(
            SourceDonnee.idEntreprise == user.idUtilisateur,
            SourceDonnee.statut != "suspendu",
        )
        .order_by(SourceDonnee.id_source.desc())
        .all()
    )
    sources_data = _collect_sources_data(db, sources)

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/configuration_wizard.html",
        {
            "user": user,
            "active_page": "configurations",
            "notifications_count": 0,
            "pending_count": 0,
            "title": "Paramétrer une analyse",
            "guide": "Choisissez les données à analyser, définissez votre objectif et la fréquence d'exécution souhaitée.",
            "sources": sources_data,
            "objectifs": _OBJECTIFS,
            "frequences": _FREQUENCES,
        },
    )
    return _no_store(response)


@router.get("/dashboard/entreprise/configurations/source/{source_id}/items")
async def entreprise_configuration_source_items(
    source_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Retourne les éléments sélectionnables (colonnes CSV, tables SQL/BDD) d'une source pour l'étape 2."""
    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")
    if source.statut == "suspendu":
        raise HTTPException(status_code=403, detail="Cette source a été suspendue par un administrateur.")

    if source.type_source == "BDD":
        connexion = db.query(ConnexionBDD).filter(ConnexionBDD.id_source == source_id).first()
        tables = json.loads(connexion.tables_detectees) if connexion and connexion.tables_detectees else []
        items = [{"key": t, "label": t, "desc": "Table détectée", "expandable": True} for t in tables]
        return JSONResponse({"kind": "tables", "source_nom": source.nom, "items": items})

    dernier = (
        db.query(ImportDonnee)
        .filter(ImportDonnee.id_source == source_id)
        .order_by(ImportDonnee.date_import.desc())
        .first()
    )
    meta = {}
    if dernier and dernier.meta_json:
        try:
            meta = json.loads(dernier.meta_json)
        except Exception:
            meta = {}

    if source.type_source == "CSV":
        columns = meta.get("columns", [])
        items = [{"key": c, "label": c, "desc": "Colonne détectée"} for c in columns]
        return JSONResponse({"kind": "columns", "source_nom": source.nom, "items": items})

    # SQL : tables extraites des instructions CREATE TABLE, sinon un item unique de repli
    tables = meta.get("tables", [])
    if not tables:
        items = [{"key": source.nom, "label": source.nom, "desc": "Fichier SQL complet", "expandable": True}]
    else:
        items = [{"key": t, "label": t, "desc": "Table détectée dans le script SQL", "expandable": True} for t in tables]
    return JSONResponse({"kind": "tables", "source_nom": source.nom, "items": items})


@router.get("/dashboard/entreprise/configurations/source/{source_id}/table/{table_name}/columns")
async def entreprise_configuration_table_columns(
    source_id: int,
    table_name: str,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Liste les colonnes réelles d'une table/fichier (étape 2, drill-down).
    - BDD : interroge la base externe en direct (information_schema, via la connexion chiffrée).
    - CSV : lit les colonnes déjà détectées à l'upload (ImportDonnee.meta_json).
    - SQL : lit les colonnes extraites du script à l'upload (ImportDonnee.meta_json.columns_by_table),
      indisponible pour les imports antérieurs à cette fonctionnalité (le fichier n'est pas relu)."""
    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")
    if source.statut == "suspendu":
        raise HTTPException(status_code=403, detail="Cette source a été suspendue par un administrateur.")

    if source.type_source == "BDD":
        from cryptography.fernet import Fernet
        from sqlalchemy import create_engine, inspect as sa_inspect

        connexion = db.query(ConnexionBDD).filter(ConnexionBDD.id_source == source_id).first()
        if not connexion:
            raise HTTPException(status_code=404, detail="Connexion introuvable.")

        fernet_key = settings.DB_ENCRYPTION_KEY
        f = Fernet(fernet_key.encode())
        username = f.decrypt(connexion.user_chiffre.encode()).decode()
        password = f.decrypt(connexion.password_chiffre.encode()).decode()
        url = _build_db_url(connexion.type_sgbd, username, password, connexion.host, connexion.port, connexion.db_name)

        try:
            engine = create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
            try:
                columns = [
                    {"name": c["name"], "type": str(c["type"])}
                    for c in sa_inspect(engine).get_columns(table_name)
                ]
            finally:
                engine.dispose()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)

        return JSONResponse({"ok": True, "table": table_name, "columns": columns})

    # CSV / SQL : colonnes déjà détectées et stockées au moment de l'upload (aucune requête réseau)
    dernier = (
        db.query(ImportDonnee)
        .filter(ImportDonnee.id_source == source_id)
        .order_by(ImportDonnee.date_import.desc())
        .first()
    )
    meta = {}
    if dernier and dernier.meta_json:
        try:
            meta = json.loads(dernier.meta_json)
        except Exception:
            meta = {}

    if source.type_source == "CSV":
        col_names = meta.get("columns", [])
    else:  # SQL
        col_names = meta.get("columns_by_table", {}).get(table_name, [])

    if not col_names:
        return JSONResponse({
            "ok": False,
            "error": "Colonnes non détectées pour ce fichier. Réimportez-le pour activer la sélection par colonne.",
        }, status_code=200)

    columns = [{"name": c, "type": None} for c in col_names]
    return JSONResponse({"ok": True, "table": table_name, "columns": columns})


def _evaluer_objectifs(profil: dict) -> tuple[dict, list[dict]]:
    """Confronte le profil réel des données sélectionnées aux règles de chaque objectif.

    Un objectif temporel exige une table qui porte *à la fois* une colonne de date et
    assez de points — c'est cette table-là qui sera analysable, pas le total toutes tables
    confondues. La recommandation est déduite du profil, jamais figée."""
    lignes_max = max((t["lignes"] for t in profil.values()), default=0)
    # Une table sans ligne fait échouer l'extraction au lancement. Le profilage connaît
    # déjà le décompte : elle est nommée dès l'étape 2, pas découverte au lancement.
    tables_vides = sorted(nom for nom, t in profil.items() if t["lignes"] < 1)
    tables_datees = [t for t in profil.values() if any(c["est_date"] for c in t["colonnes"])]
    colonnes_date = sorted({
        c["nom"] for t in profil.values() for c in t["colonnes"] if c["est_date"]
    })
    # `est_mesure` exclut les identifiants : ils sont numériques sans être des grandeurs.
    colonnes_mesure = sorted({
        c["nom"] for t in profil.values() for c in t["colonnes"] if c.get("est_mesure")
    })
    colonnes_identifiants = sorted({
        c["nom"] for t in profil.values() for c in t["colonnes"] if c.get("est_identifiant")
    })
    colonnes_booleennes = sorted({
        c["nom"] for t in profil.values() for c in t["colonnes"] if c.get("est_booleen")
    })
    # Une série temporelle a besoin d'une date ET d'une mesure à projeter : les deux doivent
    # se trouver dans la MÊME table, sinon il n'y a rien à tracer dans le temps.
    tables_analysables = [
        t for t in tables_datees if any(c.get("est_mesure") for c in t["colonnes"])
    ]
    a_mesure = bool(colonnes_mesure)
    points_temporels = max((t["lignes"] for t in tables_analysables), default=0)

    # Le volume ne rend JAMAIS un objectif indisponible : seule l'impossibilité mathématique
    # le fait (pas de date, pas de mesure, aucune donnée). Le volume qualifie la fiabilité.
    # Quand rien n'est mesurable, la raison dépend de ce qui a été écarté.
    cle_mesure = _CLES_SANS_MESURE[(bool(colonnes_identifiants), bool(colonnes_booleennes))]

    evalues = []
    for obj in _OBJECTIFS:
        etat, raison, resolution, fiabilite = "compatible", None, None, None
        if obj["temporel"]:
            if not tables_datees:
                etat, raison, resolution = "indisponible", *_BLOCAGES["sans_date"]
            elif not tables_analysables:
                etat, raison, resolution = "indisponible", *_BLOCAGES[cle_mesure]
            else:
                fiabilite = evaluer_fiabilite(points_temporels)
        elif lignes_max < 1:
            etat, raison, resolution = "indisponible", *_BLOCAGES["sans_donnee"]
        else:
            fiabilite = evaluer_fiabilite(lignes_max)
        evalues.append({
            **obj,
            "etat": etat,
            "raison": raison,
            "resolution": resolution,
            "fiabilite": fiabilite,
        })

    # Recommandation : la prévision dès que l'historique la rend fiable, sinon la comparaison.
    prefere = "prevision_evolution" if points_temporels >= SEUIL_FIABILITE_BONNE else "comparaison_classement"
    for o in evalues:
        if o["val"] == prefere and o["etat"] == "compatible":
            o["etat"] = "recommande"
            break
    else:
        for o in evalues:  # repli : le premier compatible fait office de recommandé
            if o["etat"] == "compatible":
                o["etat"] = "recommande"
                break

    # Points obtenus à chaque pas de temps : guide le choix de la fréquence sans l'imposer.
    # « ponctuelle » n'agrège pas — ses points sont les lignes elles-mêmes, ce qui en fait
    # presque toujours la fréquence la plus fiable. Elle a droit au même indicateur que les
    # autres : un affichage à deux régimes serait incohérent.
    # `adequation` est ce que l'interface affiche ; `points` et `fiabilite` restent
    # transmis pour le diagnostic, mais ne sont plus le message principal.
    def _cadence(n: int) -> dict:
        return {
            "points": n,
            "fiabilite": evaluer_fiabilite(n),
            "adequation": adequation_frequence(n),
        }

    lignes_analysables = max((t["lignes"] for t in tables_analysables), default=lignes_max)
    par_frequence = {"ponctuelle": _cadence(lignes_analysables)}
    for freq in PAS_PAR_FREQUENCE:
        n = max((t.get("points_par_frequence", {}).get(freq, 0) for t in tables_analysables), default=0)
        par_frequence[freq] = _cadence(n)

    resume = {
        "lignes": lignes_max,
        "a_date": bool(tables_datees),
        "a_mesure": a_mesure,
        "analysable": bool(tables_analysables),
        "tables_vides": tables_vides,
        "colonnes_date": colonnes_date,
        "colonnes_mesure": colonnes_mesure,
        # Le bandeau n'énumère plus les colonnes — il tient en une phrase. Ces listes
        # servent à choisir *quelle* phrase afficher, et au diagnostic via les logs.
        "colonnes_identifiants": colonnes_identifiants,
        "colonnes_booleennes": colonnes_booleennes,
        "points_temporels": points_temporels,
        "fiabilite": evaluer_fiabilite(points_temporels if tables_analysables else lignes_max),
        "par_frequence": par_frequence,
        "nb_tables": len(profil),
    }
    return resume, evalues


@router.post("/dashboard/entreprise/configurations/source/{source_id}/profil")
async def entreprise_configuration_profil(
    source_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Profil des données sélectionnées à l'étape 2 + compatibilité de chaque objectif.

    Alimente le guidage contextuel du wizard. Lit un échantillon des données réelles via
    le moteur d'analyse (mêmes lecteurs que l'extraction) : ce qui est annoncé ici est
    exactement ce que produira la tâche 4.1."""
    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")

    items = payload.get("items") or []
    selection = _build_facteurs_selectionnes(source_id, items).get("tables", {})
    if not selection:
        return JSONResponse({"ok": False, "error": "Aucune donnée sélectionnée."}, status_code=200)

    try:
        profil = profiler_selection(db, source, selection)
    except ExtractionError as exc:
        return JSONResponse({"ok": False, "error": exc.message_complet()}, status_code=200)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Analyse impossible des données : {exc}"}, status_code=200)

    resume, objectifs = _evaluer_objectifs(profil)

    # Le bandeau du wizard ne montre qu'une phrase : le détail du profil vit ici, pour
    # comprendre après coup pourquoi une colonne n'a pas été retenue comme mesure.
    _log.info(
        "[profil] source %s (%s) : %s table(s), %s ligne(s) max | dates=%s | mesures=%s "
        "| identifiants ecartes=%s | oui/non ecartes=%s | tables vides=%s",
        source_id, source.type_source, resume["nb_tables"], resume["lignes"],
        resume["colonnes_date"] or "-", resume["colonnes_mesure"] or "-",
        resume["colonnes_identifiants"] or "-", resume["colonnes_booleennes"] or "-",
        resume["tables_vides"] or "-",
    )
    return JSONResponse({"ok": True, "resume": resume, "objectifs": objectifs, "profil": profil})


@router.post("/dashboard/entreprise/configurations/draft")
async def entreprise_configuration_draft(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Étape 1+2 confirmées : enregistre la sélection de données comme brouillon de configuration."""
    body = await request.json()
    source_id = body.get("source_id")
    items = body.get("items", [])

    if not source_id or not items:
        raise HTTPException(status_code=422, detail="Source et sélection requises.")

    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")
    if source.statut == "suspendu":
        raise HTTPException(status_code=403, detail="Cette source a été suspendue par un administrateur.")

    dernier_import = (
        db.query(ImportDonnee)
        .filter(ImportDonnee.id_source == source_id)
        .order_by(ImportDonnee.date_import.desc())
        .first()
    )
    if not dernier_import:
        raise HTTPException(status_code=422, detail="Aucun import associé à cette source.")

    config = ConfigurationAnalyse(
        objectif="",
        id_import=dernier_import.id_import,
        facteurs_selectionnes=json.dumps(_build_facteurs_selectionnes(source_id, items)),
        statut="brouillon",
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    return JSONResponse({"ok": True, "id_configuration": config.id_configuration})


@router.post("/dashboard/entreprise/configurations/{config_id}/finalize")
async def entreprise_configuration_finalize(
    config_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Étape 3 : finalise la configuration (objectif + fréquence + précision libre) et l'active."""
    body = await request.json()
    objectif = (body.get("objectif") or "").strip()
    frequence = (body.get("frequence") or "").strip()
    besoin = (body.get("besoin") or "").strip()

    valid_frequences = {f[0] for f in _FREQUENCES}
    # Un objectif OU un besoin suffit, mais il en faut au moins un : sans l'un des deux,
    # il n'y a rien à traduire en analyse.
    if not objectif and not besoin:
        raise HTTPException(
            status_code=422,
            detail="Choisissez un objectif ou décrivez votre besoin — au moins l'un des deux.",
        )
    if objectif and objectif not in _OBJECTIF_KEYS:
        raise HTTPException(status_code=422, detail="Objectif invalide.")
    if frequence not in valid_frequences:
        raise HTTPException(status_code=422, detail="Fréquence invalide.")
    if len(besoin) > 2000:
        raise HTTPException(status_code=422, detail="Besoin trop long (2000 caractères max).")

    config = (
        db.query(ConfigurationAnalyse)
        .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .filter(
            ConfigurationAnalyse.id_configuration == config_id,
            SourceDonnee.idEntreprise == user.idUtilisateur,
        )
        .first()
    )
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable.")

    config.objectif = objectif
    config.frequence = frequence
    config.besoin = besoin or None
    config.statut = "actif"
    # Horodaté ici et nulle part ailleurs : /finalize est le seul point par lequel une
    # configuration change (création comme modification). La page de résultats s'en sert
    # pour ne pas présenter un changement de paramétrage comme une évolution métier.
    config.date_modification = datetime.now()
    db.commit()

    # Traduction de l'intention dès la finalisation, avant tout calcul : l'entreprise
    # doit pouvoir vérifier ce que le système a compris sans avoir rien lancé.
    reformulation = _traduire_et_stocker(db, config)

    return JSONResponse({
        "ok": True,
        "redirect": "/dashboard/entreprise/configurations",
        "intention_reformulee": reformulation,
    })


def _echec_traduction(db: Session, config: ConfigurationAnalyse, motif: str) -> None:
    """Enregistre un échec de traduction — en base ET dans les logs.

    `intention_erreur` est une colonne dédiée : partager `message_execution` avec 4.1/4.2
    faisait écraser la trace dès le premier lancement réussi, rendant l'échec invisible.
    """
    _log.warning("[traduction] cfg %s : ECHEC — %s", config.id_configuration, motif)
    config.specification_json = None
    config.intention_reformulee = None
    config.intention_erreur = motif
    db.commit()


def _traduire_et_stocker(db: Session, config: ConfigurationAnalyse) -> str | None:
    """Traduit le besoin en spécification et la stocke sur la configuration.

    Ne lève jamais — une traduction indisponible ne doit pas empêcher d'enregistrer une
    configuration par ailleurs valide — mais **ne se tait jamais non plus** : tout échec
    est journalisé et écrit dans `intention_erreur`.
    """
    _log.info(
        "[traduction] cfg %s : début (objectif=%r, besoin=%r)",
        config.id_configuration, config.objectif or None, (config.besoin or "")[:60],
    )
    try:
        imp = db.query(ImportDonnee).filter(ImportDonnee.id_import == config.id_import).first()
        source = db.query(SourceDonnee).filter(SourceDonnee.id_source == imp.id_source).first() if imp else None
        if not source:
            _echec_traduction(db, config, "Source introuvable pour cette configuration.")
            return None

        facteurs = json.loads(config.facteurs_selectionnes or "{}")
        selection = facteurs.get("tables", {}) if isinstance(facteurs, dict) else {}
        if not selection:
            _echec_traduction(db, config, "Aucune donnée sélectionnée : rien à interpréter.")
            return None

        profil = profiler_selection(db, source, selection)
        resultat = traduire_intention(config.besoin or "", config.objectif or None, profil)
    except SpecificationInvalide as exc:
        _echec_traduction(db, config, exc.message_complet())
        return None
    except Exception as exc:
        # Profilage impossible (source injoignable, fichier absent…). On n'empêche pas
        # l'enregistrement, mais on garde une trace complète pour le diagnostic.
        _log.exception("[traduction] cfg %s : erreur inattendue", config.id_configuration)
        _echec_traduction(db, config, f"Interprétation impossible : {exc}")
        return None

    config.specification_json = json.dumps(resultat.resume(), ensure_ascii=False)
    config.intention_reformulee = resultat.reformulation
    config.intention_erreur = None
    db.commit()
    _log.info(
        "[traduction] cfg %s : OK (source=%s) — %s",
        config.id_configuration, resultat.source, resultat.reformulation,
    )
    return resultat.reformulation


def _get_owned_config(db: Session, config_id: int, user: Utilisateur) -> ConfigurationAnalyse | None:
    return (
        db.query(ConfigurationAnalyse)
        .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .filter(
            ConfigurationAnalyse.id_configuration == config_id,
            SourceDonnee.idEntreprise == user.idUtilisateur,
        )
        .first()
    )


@router.get("/dashboard/entreprise/configurations/{config_id}/edit")
def entreprise_configuration_edit(
    config_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Ouvre l'assistant de configuration pré-rempli avec les valeurs d'une configuration existante."""
    config = _get_owned_config(db, config_id, user)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable.")

    dernier_import = db.query(ImportDonnee).filter(ImportDonnee.id_import == config.id_import).first()
    source_id = dernier_import.id_source if dernier_import else None

    sources = (
        db.query(SourceDonnee)
        .filter(
            SourceDonnee.idEntreprise == user.idUtilisateur,
            SourceDonnee.statut != "suspendu",
        )
        .order_by(SourceDonnee.id_source.desc())
        .all()
    )
    sources_data = _collect_sources_data(db, sources)

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/configuration_wizard.html",
        {
            "user": user,
            "active_page": "configurations",
            "notifications_count": 0,
            "pending_count": 0,
            "title": "Modifier la configuration",
            "guide": "Ajustez la source, les données, l'objectif ou la fréquence de cette analyse.",
            "sources": sources_data,
            "objectifs": _OBJECTIFS,
            "frequences": _FREQUENCES,
            "edit_config": {
                "id": config.id_configuration,
                "source_id": source_id,
                "items": _flatten_facteurs(config.facteurs_selectionnes),
                "objectif": config.objectif,
                "frequence": config.frequence,
                "besoin": config.besoin or "",
            },
        },
    )
    return _no_store(response)


@router.post("/dashboard/entreprise/configurations/{config_id}/update-selection")
async def entreprise_configuration_update_selection(
    config_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Étapes 1+2 d'une édition : met à jour la source et la sélection de données d'une configuration existante."""
    config = _get_owned_config(db, config_id, user)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable.")

    body = await request.json()
    source_id = body.get("source_id")
    items = body.get("items", [])
    if not source_id or not items:
        raise HTTPException(status_code=422, detail="Source et sélection requises.")

    source = db.query(SourceDonnee).filter(
        SourceDonnee.id_source == source_id,
        SourceDonnee.idEntreprise == user.idUtilisateur,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable.")
    if source.statut == "suspendu":
        raise HTTPException(status_code=403, detail="Cette source a été suspendue par un administrateur.")

    dernier_import = (
        db.query(ImportDonnee)
        .filter(ImportDonnee.id_source == source_id)
        .order_by(ImportDonnee.date_import.desc())
        .first()
    )
    if not dernier_import:
        raise HTTPException(status_code=422, detail="Aucun import associé à cette source.")

    config.id_import = dernier_import.id_import
    config.facteurs_selectionnes = json.dumps(_build_facteurs_selectionnes(source_id, items))
    config.statut = "brouillon"  # repasse en brouillon tant que l'étape 3 n'est pas re-confirmée
    db.commit()

    return JSONResponse({"ok": True, "id_configuration": config.id_configuration})


@router.post("/dashboard/entreprise/configurations/{config_id}/duplicate")
async def entreprise_configuration_duplicate(
    config_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Duplique une configuration existante (source, données, objectif, fréquence, précision)."""
    config = _get_owned_config(db, config_id, user)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable.")

    # La copie reprend le besoin ET sa traduction : à objectif, besoin et sélection
    # identiques, la spécification l'est aussi — inutile de refaire un appel au LLM.
    duplicate = ConfigurationAnalyse(
        objectif=config.objectif,
        id_import=config.id_import,
        facteurs_selectionnes=config.facteurs_selectionnes,
        frequence=config.frequence,
        besoin=config.besoin,
        specification_json=config.specification_json,
        intention_reformulee=config.intention_reformulee,
        statut="actif",
    )
    db.add(duplicate)
    db.commit()
    db.refresh(duplicate)

    return JSONResponse({"ok": True, "id_configuration": duplicate.id_configuration})


@router.post("/dashboard/entreprise/configurations/{config_id}/delete")
async def entreprise_configuration_delete(
    config_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Supprime définitivement une configuration d'analyse."""
    config = _get_owned_config(db, config_id, user)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable.")

    db.delete(config)
    db.commit()

    return JSONResponse({"ok": True})


_FREQUENCE_DELTAS = {
    "quotidienne": timedelta(days=1),
    "hebdomadaire": timedelta(weeks=1),
    "mensuelle": timedelta(days=30),
}


# L'extraction est synchrone dans la requête : passé ce délai, un statut « extraction en
# cours » ne peut plus correspondre à un traitement réellement en vie.
DELAI_EXECUTION_BLOQUEE = timedelta(minutes=5)


def _reparer_executions_bloquees(db: Session, configs: list) -> None:
    """Rattrape les exécutions mortes en cours de route.

    `executer_extraction` écrit « extraction en cours » en base *avant* de lire la source.
    Si la requête n'arrive jamais à son terme (serveur redémarré — fréquent avec
    `uvicorn --reload` —, onglet fermé, délai dépassé), ce statut reste figé et le badge
    bleu ne redescend jamais. On le convertit en erreur explicite au rendu de la liste.
    """
    seuil = datetime.utcnow() - DELAI_EXECUTION_BLOQUEE
    repare = False
    for c in configs:
        if c.statut_execution != STATUT_EXTRACTION_EN_COURS:
            continue
        if c.derniere_execution is None or c.derniere_execution < seuil:
            c.statut_execution = STATUT_ERREUR_DONNEES
            c.message_execution = (
                "L'exécution a été interrompue avant la fin : le serveur a redémarré, "
                "ou le traitement a dépassé le délai. Relancez l'analyse."
            )
            repare = True
    if repare:
        db.commit()


def _diagnostiquer_objectif(resultat, objectif: str) -> str | None:
    """Vérifie a posteriori que l'objectif reste tenable avec les données extraites.

    Une configuration valide à sa création peut devenir incompatible (colonne de date
    supprimée, historique tronqué). Le message produit *guide* — il dit ce qui manque et
    quelle action débloque — plutôt que de constater l'échec."""
    obj = _OBJECTIF_BY_VAL.get(objectif)
    if not obj:
        return None  # objectif obsolète : le fallback l'affiche déjà en gris, rien à diagnostiquer

    # `decrire_colonnes` et non une description reconstruite ici : ce profil est confronté
    # aux MÊMES règles que celui du wizard (`_evaluer_objectifs`). Une description locale
    # avait omis `est_mesure`, et tout objectif temporel devenait irréalisable au
    # lancement alors que le wizard l'annonçait compatible. Deux producteurs de profil,
    # une seule fonction : ils ne peuvent plus diverger.
    profil = {
        nom: {"lignes": len(df), "colonnes": decrire_colonnes(df)}
        for nom, df in resultat.donnees.items()
    }
    resume, evalues = _evaluer_objectifs(profil)
    etat = next((o for o in evalues if o["val"] == objectif), None)
    if not etat or etat["etat"] != "indisponible":
        return None

    # Seuls les cas mathématiquement impossibles arrivent ici : le volume ne bloque plus.
    label = obj["label"]
    alternatives = [o["label"] for o in evalues if o["etat"] in ("compatible", "recommande")]
    remede = (
        "Sélectionnez une colonne de date dans la configuration"
        if not resume["a_date"]
        else "Ajoutez une colonne chiffrée (quantité, montant, total…) à la configuration"
    )
    message = (
        f"L'objectif « {label} » n'est plus réalisable avec ces données : {etat['raison'].lower()}. "
        f"{remede}"
    )
    if alternatives:
        message += f", ou choisissez un objectif adapté : {', '.join(alternatives)}."
    else:
        message += "."
    return message


def _echec_lancement(db: Session, config: ConfigurationAnalyse, message: str, missing: list[str] | None = None) -> JSONResponse:
    """Enregistre un échec de lancement en base (pour qu'il survive au rechargement de la page)
    et renvoie la réponse d'erreur correspondante."""
    config.statut_execution = STATUT_ERREUR_DONNEES
    config.message_execution = message
    db.commit()
    corps = {"ok": False, "statut_execution": STATUT_ERREUR_DONNEES, "error": message}
    if missing:
        corps["missing"] = missing
    return JSONResponse(corps, status_code=200)


def _verifier_disponibilite_facteurs(db: Session, source: SourceDonnee, tables: dict) -> list[str]:
    """Vérifie que les tables/colonnes sélectionnées à l'étape 2 existent toujours.
    Retourne la liste des éléments manquants (vide = tout est disponible)."""
    manquants: list[str] = []

    if source.type_source == "BDD":
        from cryptography.fernet import Fernet
        from sqlalchemy import create_engine, inspect as sa_inspect

        connexion = db.query(ConnexionBDD).filter(ConnexionBDD.id_source == source.id_source).first()
        if not connexion:
            return [f"Connexion « {source.nom} » introuvable"]
        try:
            fernet_key = settings.DB_ENCRYPTION_KEY
            f = Fernet(fernet_key.encode())
            username = f.decrypt(connexion.user_chiffre.encode()).decode()
            password = f.decrypt(connexion.password_chiffre.encode()).decode()
            url = _build_db_url(connexion.type_sgbd, username, password, connexion.host, connexion.port, connexion.db_name)
            engine = create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
            try:
                insp = sa_inspect(engine)
                live_tables = set(insp.get_table_names())
                for table, cols in tables.items():
                    if table not in live_tables:
                        manquants.append(f"Table « {table} »")
                        continue
                    if isinstance(cols, list):
                        live_cols = {c["name"] for c in insp.get_columns(table)}
                        for col in cols:
                            if col not in live_cols:
                                manquants.append(f"Colonne « {table}.{col} »")
            finally:
                engine.dispose()
        except Exception as exc:
            manquants.append(f"Connexion à « {source.nom} » impossible : {exc}")
        return manquants

    # CSV / SQL : comparer aux dernières métadonnées connues (pas de requête réseau)
    dernier = (
        db.query(ImportDonnee)
        .filter(ImportDonnee.id_source == source.id_source)
        .order_by(ImportDonnee.date_import.desc())
        .first()
    )
    meta = {}
    if dernier and dernier.meta_json:
        try:
            meta = json.loads(dernier.meta_json)
        except Exception:
            meta = {}

    if source.type_source == "CSV":
        # Pour un CSV, les clés de "tables" sont en réalité des noms de colonnes (sélection à plat)
        known_columns = set(meta.get("columns", []))
        for col in tables.keys():
            if col not in known_columns:
                manquants.append(f"Colonne « {col} »")
    else:  # SQL
        known_tables = set(meta.get("tables", []))
        columns_by_table = meta.get("columns_by_table", {})
        for table, cols in tables.items():
            if known_tables and table not in known_tables:
                manquants.append(f"Table « {table} »")
                continue
            if isinstance(cols, list) and table in columns_by_table:
                known_cols = set(columns_by_table[table])
                for col in cols:
                    if col not in known_cols:
                        manquants.append(f"Colonne « {table}.{col} »")

    return manquants


@router.post("/dashboard/entreprise/configurations/{config_id}/lancer")
async def entreprise_configuration_lancer(
    config_id: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Lance une configuration d'analyse : vérifie la disponibilité des données, prépare le
    payload pour le Module 4 (pas encore développé) et passe la configuration en attente d'exécution."""
    config = _get_owned_config(db, config_id, user)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable.")
    if config.statut != "actif":
        raise HTTPException(status_code=422, detail="Cette configuration doit être finalisée avant d'être lancée.")

    dernier_import = db.query(ImportDonnee).filter(ImportDonnee.id_import == config.id_import).first()
    source = db.query(SourceDonnee).filter(SourceDonnee.id_source == dernier_import.id_source).first() if dernier_import else None
    if not source:
        raise HTTPException(status_code=422, detail="La source liée à cette configuration est introuvable.")
    if source.statut == "suspendu":
        return _echec_lancement(
            db, config, f"La source « {source.nom} » a été suspendue par un administrateur."
        )

    try:
        facteurs = json.loads(config.facteurs_selectionnes or "{}")
    except Exception:
        facteurs = {}
    tables = facteurs.get("tables", {}) if isinstance(facteurs, dict) else {}

    # 1. Vérifier que les données sélectionnées existent toujours
    manquants = _verifier_disponibilite_facteurs(db, source, tables)
    if manquants:
        return _echec_lancement(
            db, config,
            "Certaines données sélectionnées ne sont plus disponibles : " + ", ".join(manquants) + ".",
            missing=manquants,
        )

    # 2. Horodatage du lancement (le statut d'exécution est piloté par l'extraction, étape 4)
    now = datetime.utcnow()
    config.derniere_execution = now
    config.prochaine_execution = (now + _FREQUENCE_DELTAS[config.frequence]) if config.frequence in _FREQUENCE_DELTAS else None

    # 3. Payload conservé comme trace du lancement (les données sont relues à la source par 4.1)
    obj_meta = _OBJECTIF_BY_VAL.get(config.objectif, _OBJECTIF_FALLBACK)
    payload = {
        "config_id": config.id_configuration,
        "source": {
            "id_source": source.id_source,
            "nom": source.nom,
            "type": source.type_source,
        },
        "donnees_selectionnees": tables,
        "objectif": {
            "code": config.objectif,
            "label": _OBJECTIF_LABELS.get(config.objectif, config.objectif),
        },
        "besoin": config.besoin or "",
        "frequence": config.frequence,
        "genere_le": now.isoformat(),
    }
    config.payload_execution = json.dumps(payload, ensure_ascii=False)
    db.commit()

    # 3 bis. Traduction : rattrape les configurations jamais traduites (créées avant la
    # couche de traduction) ou dont la traduction avait échoué — par exemple parce que la
    # clé API manquait alors et vient d'être ajoutée.
    if not config.intention_reformulee:
        _traduire_et_stocker(db, config)

    # 4. Module 4 — tâche 4.1 : extraction des données sélectionnées.
    # Exécution synchrone (aucun scheduler/worker à ce stade) : la requête reste ouverte
    # le temps de lire la source, comme _list_tables ailleurs dans ce fichier.
    resultat = executer_extraction(db, config)
    if resultat is None:
        return JSONResponse({
            "ok": False,
            "statut_execution": config.statut_execution,
            "error": config.message_execution,
        }, status_code=200)

    # 5. L'extraction a réussi, mais l'objectif est-il encore tenable avec ces données ?
    diagnostic = _diagnostiquer_objectif(resultat, config.objectif)
    if diagnostic:
        return _echec_lancement(db, config, diagnostic)

    # 6. Tâche 4.2 — préparation. Écrit elle-même le statut et le message d'exécution.
    prepare = executer_preparation(db, config, resultat)
    if prepare is None:
        return JSONResponse({
            "ok": False,
            "statut_execution": config.statut_execution,
            "error": config.message_execution,
        }, status_code=200)

    # 7. Exécution — le moteur de calcul. La spécification vient de la traduction déjà
    # stockée : aucun appel LLM supplémentaire au lancement.
    calcul = None
    if not config.specification_json:
        config.execution_erreur = (
            "Aucune spécification d'analyse n'est disponible : l'interprétation de votre "
            "besoin n'a pas abouti. Consultez le détail de cette configuration."
        )
        db.commit()
    else:
        try:
            intention = intention_depuis_resume(json.loads(config.specification_json))
        except SpecificationInvalide as exc:
            _log.warning("[execution] cfg %s : spécification illisible — %s",
                         config.id_configuration, exc.message_complet())
            config.execution_erreur = exc.message_complet()
            db.commit()
        else:
            calcul = executer_et_stocker(db, config, intention, prepare)

    # 8. Tâche 4.5 — restitution en langage naturel. Ne s'exécute que si le calcul a
    # produit un résultat : il n'y a rien à commenter autrement. Ne lève jamais, et
    # l'indisponibilité du service externe ne dégrade pas le lancement — les chiffres
    # sont déjà en base, seule la rédaction manque.
    interpretation = None
    if calcul is not None:
        interpretation = interpreter_et_stocker(db, config, calcul)

    return JSONResponse({
        "ok": True,
        "statut_execution": config.statut_execution,
        "message": config.message_execution,
        "derniere_execution": config.derniere_execution.strftime("%d/%m/%Y %H:%M") if config.derniere_execution else None,
        "prochaine_execution": config.prochaine_execution.strftime("%d/%m/%Y %H:%M") if config.prochaine_execution else None,
        "extraction": resultat.resume(),
        "preparation": prepare.resume(),
        # Le calcul peut échouer sans invalider extraction et préparation : on renvoie le
        # motif plutôt que de faire passer tout le lancement pour un échec.
        "execution": calcul.resume() if calcul else None,
        "execution_erreur": config.execution_erreur,
        # `disponible: false` n'est pas un échec du lancement : c'est un texte manquant
        # au-dessus de chiffres valides. L'interface doit les distinguer.
        "interpretation": interpretation.resume() if interpretation else None,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Module 4 — page de résultats
# ──────────────────────────────────────────────────────────────────────────────
#
# Rend visible tout ce que la chaîne 4.1 → 4.6 a produit : chiffres, intervalles,
# indicateurs, criticité, interprétation. Déclarée AVANT la route générique
# `/{section}`, qui l'absorberait sinon (FastAPI apparie dans l'ordre de déclaration).

# Grandeur comparable d'une exécution à l'autre, par type d'analyse. Comparer deux
# résultats n'a de sens que sur une même grandeur — d'où ce choix explicite plutôt qu'une
# diff générique sur tous les indicateurs.
_COMPARABLE_PAR_TYPE: dict[str, tuple[str, str]] = {
    "prevision": ("valeur_prevue", "valeur prévue"),
    "tendance": ("variation_pct", "variation"),
    "anomalie": ("nb_anomalies", "anomalies"),
    "classement": ("concentration", "concentration"),
}


def _fmt_nombre(valeur, decimales: int = 2) -> str:
    """Nombre lisible : espace fine insécable comme séparateur de milliers, virgule décimale."""
    if valeur is None:
        return "—"
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return str(valeur)
    if nombre == int(nombre) and abs(nombre) < 1e15:
        texte = f"{int(nombre):,}".replace(",", " ")
        return texte
    texte = f"{nombre:,.{decimales}f}".replace(",", " ").replace(".", ",")
    return texte


def _fmt_pct(valeur, decimales: int = 1) -> str:
    if valeur is None:
        return "—"
    try:
        return f"{float(valeur):.{decimales}f}".replace(".", ",") + " %"
    except (TypeError, ValueError):
        return str(valeur)


def _indicateurs_cles(type_analyse: str, ind: dict, ligne, fiabilite: dict) -> list[dict]:
    """Trois à quatre valeurs, choisies selon ce que l'opération produit réellement.

    Un résultat non concluant ne met **jamais** sa variation en avant : la donner comme
    chiffre clé reviendrait à affirmer ce que le calcul refuse de soutenir.
    """
    concluant = bool(ind.get("conclusif"))
    fiab = {
        "libelle": "Fiabilité",
        "valeur": (fiabilite.get("label") or "—").replace("Fiabilité ", "").capitalize(),
        "detail": f"{fiabilite.get('points', '—')} points",
        "ton": fiabilite.get("code") or "indicative",
    }

    if type_analyse == "prevision":
        return [
            {"libelle": "Dernière valeur observée", "valeur": _fmt_nombre(ind.get("valeur_depart")),
             "detail": ligne.get("unite") or ""},
            {"libelle": "Valeur prévue", "valeur": _fmt_nombre(ligne.get("valeur_prevue")),
             "detail": (_fmt_pct(ind.get("variation_pct")) + " d'écart") if concluant
                       else "sens non déterminé"},
            {"libelle": "Intervalle de confiance",
             "valeur": f"{_fmt_nombre(ligne.get('intervalle_bas'))} – {_fmt_nombre(ligne.get('intervalle_haut'))}",
             "detail": "estimé sur les résidus" if ligne.get("methode_intervalle") == "residus"
                       else "fourni par le modèle"},
            fiab,
        ]
    if type_analyse == "tendance":
        return [
            {"libelle": "Valeur de départ", "valeur": _fmt_nombre(ind.get("valeur_depart")),
             "detail": ligne.get("unite") or ""},
            {"libelle": "Valeur d'arrivée", "valeur": _fmt_nombre(ind.get("valeur_arrivee")),
             "detail": ligne.get("unite") or ""},
            {"libelle": "Évolution sur la période",
             "valeur": _fmt_pct(ind.get("variation_pct")) if concluant else "non déterminée",
             "detail": f"ajustement R² = {_fmt_nombre(ind.get('r2'))}"},
            fiab,
        ]
    if type_analyse == "anomalie":
        return [
            {"libelle": "Observations inhabituelles", "valeur": _fmt_nombre(ind.get("nb_anomalies")),
             "detail": f"sur {_fmt_nombre(ind.get('nb_observations'))} mesures"},
            {"libelle": "Valeur moyenne", "valeur": _fmt_nombre(ind.get("moyenne")),
             "detail": ligne.get("unite") or ""},
            {"libelle": "Écart-type", "valeur": _fmt_nombre(ind.get("ecart_type")),
             "detail": f"seuil retenu : {_fmt_nombre(ind.get('seuil_ecarts_types'))} σ"},
            fiab,
        ]
    # classement
    concentration = ind.get("concentration")
    return [
        {"libelle": "En tête du classement", "valeur": str(ind.get("premier") or "—"),
         "detail": f"{_fmt_nombre(ind.get('valeur_premier'))} {ligne.get('unite') or ''}".strip()},
        {"libelle": "Part du total",
         "valeur": _fmt_pct(concentration * 100) if concentration is not None else "—",
         "detail": "capté par le premier"},
        {"libelle": "Éléments comparés", "valeur": _fmt_nombre(ind.get("nb_elements")),
         "detail": f"écart premier/dernier : {_fmt_nombre(ind.get('ecart_premier_dernier'))}"},
        fiab,
    ]


def _evolution(courant: dict, precedent: dict | None, config) -> dict | None:
    """Évolution par rapport à l'exécution précédente de la MÊME configuration.

    Renvoie `None` — et c'est le cœur de cette fonction — dès qu'une comparaison serait
    trompeuse. Un delta faux est pire que pas de delta :

    1. l'une des deux exécutions n'est pas concluante : on ne compare pas des incertitudes ;
    2. le type d'analyse a changé (configuration retraduite) : rien de comparable ;
    3. la fréquence a changé : le pas d'agrégation diffère, les points ne se comparent pas ;
    4. la configuration a été modifiée entre les deux : ce serait un changement de
       paramétrage présenté comme une évolution métier.
    """
    if precedent is None:
        return None
    if not courant["ind"].get("conclusif") or not precedent["ind"].get("conclusif"):
        return None
    if courant["type_analyse"] != precedent["type_analyse"]:
        return {"bloque": "Type d'analyse modifié depuis la dernière exécution."}

    modifiee = getattr(config, "date_modification", None)
    if modifiee and precedent["date_brute"] and modifiee > precedent["date_brute"]:
        return {"bloque": "Configuration modifiée depuis la dernière exécution : "
                          "les deux résultats ne sont pas comparables."}

    type_analyse = courant["type_analyse"]

    # Sur un classement, le changement de tête prime : « Produit C a remplacé Produit A »
    # est l'information métier ; la concentration ne parle que si la tête n'a pas bougé.
    if type_analyse == "classement":
        avant, apres = precedent["ind"].get("premier"), courant["ind"].get("premier")
        if avant and apres and avant != apres:
            return {"sens": "change", "texte": f"« {apres} » a remplacé « {avant} » en tête",
                    "depuis": precedent["date"]}

    cle, libelle = _COMPARABLE_PAR_TYPE.get(type_analyse, (None, ""))
    if not cle:
        return None
    avant = precedent["ind"].get(cle) if cle != "valeur_prevue" else precedent["valeur_prevue"]
    apres = courant["ind"].get(cle) if cle != "valeur_prevue" else courant["valeur_prevue"]
    if avant is None or apres is None:
        return None
    try:
        avant, apres = float(avant), float(apres)
    except (TypeError, ValueError):
        return None

    if cle == "concentration":
        avant, apres = avant * 100, apres * 100
        formate = _fmt_pct
    elif cle == "nb_anomalies":
        formate = lambda v: _fmt_nombre(v, 0)
    elif cle == "variation_pct":
        formate = _fmt_pct
    else:
        formate = _fmt_nombre

    ecart = apres - avant
    if abs(ecart) < 1e-9:
        return {"sens": "stable", "texte": f"{libelle} inchangée ({formate(apres)})",
                "depuis": precedent["date"]}
    return {
        "sens": "hausse" if ecart > 0 else "baisse",
        "texte": f"{libelle} : {formate(avant)} → {formate(apres)}",
        "depuis": precedent["date"],
    }


@router.get("/dashboard/entreprise/resultats")
def entreprise_resultats(
    request: Request,
    criticite: str = "tous",
    config: int | None = None,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Résultats et prévisions — une carte par exécution, la plus récente dépliée."""
    title, guide = _ENTREPRISE_SECTIONS["resultats"]

    # Seuls les résultats des configurations de cette entreprise, via l'import et la source.
    lignes = (
        db.query(ResultatAnalyse, ConfigurationAnalyse)
        .join(ConfigurationAnalyse,
              ResultatAnalyse.id_configuration == ConfigurationAnalyse.id_configuration)
        .join(ImportDonnee, ConfigurationAnalyse.id_import == ImportDonnee.id_import)
        .join(SourceDonnee, ImportDonnee.id_source == SourceDonnee.id_source)
        .filter(SourceDonnee.idEntreprise == user.idUtilisateur)
        .order_by(ResultatAnalyse.date_execution.asc(), ResultatAnalyse.id_resultat.asc())
        .all()
    )

    _FIABILITE_LABELS = {code: label for _, code, label, _ in NIVEAUX_FIABILITE}
    _FREQ_LABELS = dict(_FREQUENCES)

    # Premier passage en ordre chronologique : il faut le prédécesseur de chaque exécution
    # pour calculer l'évolution. L'ordre d'affichage est inversé ensuite.
    par_config: dict[int, list[dict]] = {}
    cartes: list[dict] = []
    for res, cfg in lignes:
        resume = {}
        if res.resultat_json:
            try:
                resume = json.loads(res.resultat_json)
            except Exception:
                resume = {}
        ind = resume.get("indicateurs") or {}
        vis = resume.get("visualisation") or {}
        fiabilite = resume.get("fiabilite") or {}
        if not fiabilite.get("label") and res.fiabilite:
            fiabilite = {"code": res.fiabilite, "label": _FIABILITE_LABELS.get(res.fiabilite, ""),
                         "points": ind.get("nb_observations")}

        interpretation = {}
        if res.interpretation_json:
            try:
                interpretation = (json.loads(res.interpretation_json) or {}).get("interpretation") or {}
            except Exception:
                interpretation = {}

        presentation = _presenter_objectif(cfg)
        type_analyse = res.type_analyse or resume.get("type_analyse") or ""
        criticite_detail = resume.get("criticite") or {}

        carte = {
            "id": res.id_resultat,
            "id_configuration": cfg.id_configuration,
            "type_analyse": type_analyse,
            "date": res.date_execution.strftime("%d/%m/%Y à %H:%M") if res.date_execution else "—",
            "date_courte": res.date_execution.strftime("%d/%m") if res.date_execution else "—",
            "date_brute": res.date_execution,
            "objectif": presentation,
            "frequence": _FREQ_LABELS.get(cfg.frequence, cfg.frequence or "—"),
            "frequence_color": _FREQUENCE_COLORS.get(cfg.frequence, "#64748b"),
            # Le besoin exprimé prime sur la reformulation : c'est la question posée par
            # l'entreprise, pas ce que le système en a compris. La reformulation prend le
            # relais quand aucun besoin libre n'a été saisi.
            "demande": (cfg.besoin or "").strip() or (cfg.intention_reformulee or "").strip(),
            "demande_source": "besoin" if (cfg.besoin or "").strip() else "reformulation",
            "criticite": res.criticite or "normal",
            "criticite_rang": res.criticite_rang if res.criticite_rang is not None else 0,
            "criticite_libelle": criticite_detail.get("libelle")
                                 or LIBELLE_PAR_NIVEAU.get(res.criticite or "normal", "Normal"),
            "criticite_motif": res.criticite_motif or criticite_detail.get("motif") or "",
            "criticite_plafonnements": criticite_detail.get("plafonnements") or [],
            "concluant": bool(ind.get("conclusif")),
            "modele": LIBELLE_MODELE.get(res.modele_applique, res.modele_applique or "—"),
            "fiabilite": fiabilite,
            "unite": resume.get("unite") or "",
            "valeur_prevue": resume.get("valeur_prevue"),
            "intervalle_bas": (resume.get("intervalle") or {}).get("bas"),
            "intervalle_haut": (resume.get("intervalle") or {}).get("haut"),
            "methode_intervalle": (resume.get("intervalle") or {}).get("methode"),
            "avertissements": resume.get("avertissements") or [],
            "ind": ind,
            "interpretation": interpretation,
            "interpretation_source": res.interpretation_source,
            "interpretation_erreur": res.interpretation_erreur,
            # Séries de visualisation, telles que 4.4 les a produites : le graphique les
            # lit sans retraitement.
            "graphique": {
                "type": type_analyse,
                "historique": vis.get("serie_historique") or [],
                "prevue": vis.get("serie_prevue") or [],
                "elements": vis.get("elements_classes") or [],
                "aberrantes": vis.get("observations_aberrantes") or [],
                "concluant": bool(ind.get("conclusif")),
                "unite": resume.get("unite") or "",
            },
        }
        carte["indicateurs_cles"] = _indicateurs_cles(type_analyse, ind, carte, fiabilite)

        lignee = par_config.setdefault(cfg.id_configuration, [])
        carte["rang_lignee"] = len(lignee) + 1
        carte["evolution"] = _evolution(carte, lignee[-1] if lignee else None, cfg)
        lignee.append(carte)
        cartes.append(carte)

    for carte in cartes:
        carte["total_lignee"] = len(par_config.get(carte["id_configuration"], []))

    # Comptes calculés avant filtrage : un filtre doit annoncer ce qu'il cache.
    comptes = {code: 0 for code, _, _ in NIVEAUX_CRITICITE}
    for carte in cartes:
        comptes[carte["criticite"]] = comptes.get(carte["criticite"], 0) + 1

    total = len(cartes)
    if config is not None:
        cartes = [c for c in cartes if c["id_configuration"] == config]
    if criticite != "tous" and criticite in comptes:
        cartes = [c for c in cartes if c["criticite"] == criticite]

    cartes.reverse()   # la plus récente en premier

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/resultats.html",
        {
            "user": user,
            "active_page": "resultats",
            "notifications_count": 0,
            "pending_count": 0,
            "title": title,
            "guide": guide,
            "cartes": cartes,
            "total": total,
            "comptes": comptes,
            "niveaux": [
                {"code": code, "rang": rang, "libelle": libelle}
                for code, rang, libelle in NIVEAUX_CRITICITE
            ],
            "criticite_filtre": criticite,
            "config_filtre": config,
        },
    )
    return _no_store(response)


# ──────────────────────────────────────────────────────────────────────────────
# Module 5 — alertes
# ──────────────────────────────────────────────────────────────────────────────
#
# Déclarées AVANT `/{section}`, qui les absorberait sinon.

@router.get("/dashboard/entreprise/alertes")
def entreprise_alertes(
    request: Request,
    niveau: str = "tous",
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Mes alertes — les plus récentes d'abord, avec accès au résultat qui les a produites."""
    from app.services.alertes import (
        NIVEAUX_ALERTE,
        STATUT_TRAITEE,
        compter_par_niveau,
        lister_alertes,
    )

    title, guide = _ENTREPRISE_SECTIONS["alertes"]
    alertes = lister_alertes(db, user.idUtilisateur, niveau)

    # Objectif et fréquence de la configuration concernée : l'alerte doit dire de quelle
    # analyse elle parle, pas seulement ce qui s'est passé.
    ids_config = {a.id_configuration for a in alertes}
    configs = {
        c.id_configuration: c
        for c in db.query(ConfigurationAnalyse).filter(
            ConfigurationAnalyse.id_configuration.in_(ids_config)).all()
    } if ids_config else {}
    _FREQ_LABELS = dict(_FREQUENCES)

    donnees = []
    for a in alertes:
        cfg = configs.get(a.id_configuration)
        presentation = _presenter_objectif(cfg) if cfg else {
            "label": "Configuration supprimée", "icon": "alert-triangle",
            "color": "#64748b", "libre": False, "titre": "", "sous_ligne": "",
        }
        donnees.append({
            "id": a.id_alerte,
            "niveau": a.niveau or "eleve",
            "libelle": LIBELLE_PAR_NIVEAU.get(a.niveau, a.niveau or "—"),
            "message": a.message or "",
            "type": a.type_alerte or "",
            "id_resultat": a.id_resultat,
            "id_configuration": a.id_configuration,
            "objectif": presentation,
            "frequence": _FREQ_LABELS.get(cfg.frequence, cfg.frequence or "—") if cfg else "—",
            "date": a.date_creation.strftime("%d/%m/%Y à %H:%M") if a.date_creation else "—",
            "traitee": a.statut == STATUT_TRAITEE,
            "date_traitement": (a.date_traitement.strftime("%d/%m/%Y à %H:%M")
                                if a.date_traitement else None),
        })

    comptes = compter_par_niveau(db, user.idUtilisateur)
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/alertes.html",
        {
            "user": user, "active_page": "alertes", "notifications_count": 0,
            "pending_count": 0, "title": title, "guide": guide,
            "alertes": donnees,
            "total": sum(comptes.values()),
            "comptes": comptes,
            "niveaux": NIVEAUX_ALERTE,
            "niveau_filtre": niveau,
        },
    )
    return _no_store(response)


@router.post("/dashboard/entreprise/alertes/{id_alerte}/traiter")
async def entreprise_alerte_traiter(
    id_alerte: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    """Marque une alerte traitée — ou la remet en attente. Elle reste consultable."""
    from app.services.alertes import compter_non_lues, marquer_traitee

    try:
        corps = await request.json()
    except Exception:
        corps = {}
    traitee = bool(corps.get("traitee", True))

    if not marquer_traitee(db, id_alerte, user.idUtilisateur, traitee):
        raise HTTPException(status_code=404, detail="Alerte introuvable.")
    return JSONResponse({
        "ok": True,
        "traitee": traitee,
        "non_lues": compter_non_lues(db, user.idUtilisateur),
    })


@router.get("/dashboard/entreprise/{section}")
def entreprise_section(
    section: str,
    request: Request,
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    title, guide = _ENTREPRISE_SECTIONS.get(
        section,
        (section.replace("-", " ").title(), "Cette section arrive bientôt.")
    )
    response = templates.TemplateResponse(
        request,
        "pages/dashboard/placeholder.html",
        {
            "user": user,
            "active_page": section,
            "notifications_count": 0,
            "pending_count": 0,
            "title": title,
            "guide": guide,
        },
    )
    return _no_store(response)


@router.post("/dashboard/changer-mot-de-passe")
def changer_mot_de_passe(
    nouveau_mdp: str = Form(...),
    confirmation_mdp: str = Form(...),
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(get_current_user),
):
    if nouveau_mdp != confirmation_mdp:
        raise HTTPException(status_code=400, detail="Les mots de passe ne correspondent pas.")
    if len(nouveau_mdp) < 8:
        raise HTTPException(
            status_code=400, detail="Le mot de passe doit contenir au moins 8 caractères."
        )
    if not any(c.isupper() for c in nouveau_mdp):
        raise HTTPException(
            status_code=400, detail="Le mot de passe doit contenir au moins une majuscule."
        )
    if not any(c.isdigit() for c in nouveau_mdp):
        raise HTTPException(
            status_code=400, detail="Le mot de passe doit contenir au moins un chiffre."
        )

    user.mot_de_passe = hash_password(nouveau_mdp)
    user.doit_changer_mdp = False
    db.commit()

    new_token = create_access_token(
        {
            "sub": str(user.idUtilisateur),
            "email": user.email,
            "role": user.role.value,
            "doit_changer_mdp": False,
        }
    )

    response = JSONResponse(content={"ok": True})
    response.set_cookie(
        key="access_token",
        value=new_token,
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return response


@router.get("/dashboard/profil")
def profil(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(get_current_user),
):
    ctx: dict = {"user": user, "active_page": "profil", "notifications_count": 0}

    if user.role == RoleEnum.administrateur:
        admin = db.query(Administrateur).filter(Administrateur.idAdmin == user.idUtilisateur).first()
        total = db.query(Entreprise).count()
        en_attente = db.query(Entreprise).filter(Entreprise.statut_demande == StatutDemandeEnum.en_attente).count()
        approuvees = db.query(Entreprise).filter(Entreprise.statut_demande == StatutDemandeEnum.validee).count()
        refusees = db.query(Entreprise).filter(Entreprise.statut_demande == StatutDemandeEnum.refusee).count()
        ctx.update({
            "admin": admin,
            "stats": {
                "total": total,
                "en_attente": en_attente,
                "approuvees": approuvees,
                "refusees": refusees,
            },
            "pending_count": en_attente,
        })
        template = "pages/dashboard_admin/profil.html"

    else:
        entreprise = db.query(Entreprise).filter(Entreprise.idEntreprise == user.idUtilisateur).first()
        # Imports : via les sources de données de l'entreprise
        source_ids = [
            r[0] for r in db.query(SourceDonnee.id_source)
            .filter(SourceDonnee.idEntreprise == user.idUtilisateur).all()
        ]
        nb_imports = 0
        nb_configs = 0
        nb_alertes = 0
        if source_ids:
            import_ids = [
                r[0] for r in db.query(ImportDonnee.id_import)
                .filter(ImportDonnee.id_source.in_(source_ids)).all()
            ]
            nb_imports = len(import_ids)
            if import_ids:
                config_ids = [
                    r[0] for r in db.query(ConfigurationAnalyse.id_configuration)
                    .filter(
                        ConfigurationAnalyse.id_import.in_(import_ids),
                        ConfigurationAnalyse.statut == "actif",
                    ).all()
                ]
                nb_configs = len(config_ids)
                if config_ids:
                    nb_alertes = db.query(Alerte).filter(
                        Alerte.id_configuration.in_(config_ids)
                    ).count()
        ctx.update({
            "entreprise": entreprise,
            "stats": {
                "imports": nb_imports,
                "configurations": nb_configs,
                "alertes": nb_alertes,
            },
        })
        template = "pages/dashboard_entreprise/profil.html"

    return _no_store(templates.TemplateResponse(request, template, ctx))


@router.post("/dashboard/profil/modifier-infos")
def profil_modifier_infos(
    nom: str = Form(...),
    secteur_activite: str = Form(None),
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(get_current_user),
):
    if user.role == RoleEnum.administrateur:
        admin = db.query(Administrateur).filter(Administrateur.idAdmin == user.idUtilisateur).first()
        if admin:
            admin.nom = nom.strip()
    else:
        entreprise = db.query(Entreprise).filter(Entreprise.idEntreprise == user.idUtilisateur).first()
        if entreprise:
            entreprise.nom = nom.strip()
            if secteur_activite is not None:
                entreprise.secteur_activite = secteur_activite.strip()
    db.commit()
    return JSONResponse(content={"ok": True})


@router.post("/dashboard/profil/changer-mot-de-passe")
def profil_changer_mdp(
    ancien_mdp: str = Form(...),
    nouveau_mdp: str = Form(...),
    confirmation_mdp: str = Form(...),
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(get_current_user),
):
    if not user.mot_de_passe or not verify_password(ancien_mdp, user.mot_de_passe):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect.")
    if nouveau_mdp != confirmation_mdp:
        raise HTTPException(status_code=400, detail="Les mots de passe ne correspondent pas.")
    if len(nouveau_mdp) < 8:
        raise HTTPException(status_code=400, detail="Le mot de passe doit contenir au moins 8 caractères.")
    if not any(c.isupper() for c in nouveau_mdp):
        raise HTTPException(status_code=400, detail="Le mot de passe doit contenir au moins une majuscule.")
    if not any(c.isdigit() for c in nouveau_mdp):
        raise HTTPException(status_code=400, detail="Le mot de passe doit contenir au moins un chiffre.")

    user.mot_de_passe = hash_password(nouveau_mdp)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.get("/deconnexion")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    return response
