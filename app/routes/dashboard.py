import csv
import io
import json
import os
import re
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["asset_version"] = str(int(time.time()))

router = APIRouter(tags=["dashboard"])

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
    pending_count = _pending_count(db)
    entreprises_count = (
        db.query(Entreprise).filter(Entreprise.statut_demande == StatutDemandeEnum.validee).count()
    )
    utilisateurs_count = db.query(Utilisateur).count()

    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_overview.html",
        {
            "user": user,
            "active_page": "overview",
            "pending_count": pending_count,
            "entreprises_count": entreprises_count,
            "utilisateurs_count": utilisateurs_count,
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


@router.get("/dashboard/admin/entreprises")
def admin_entreprises(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
    statut: str = "validee",
    q: str = "",
):
    entreprises = entreprise_service.list_entreprises(db, statut=statut, q=q)
    pending_count = _pending_count(db)
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_admin/admin_entreprises.html",
        {
            "user": user,
            "active_page": "entreprises",
            "pending_count": pending_count,
            "notifications_count": pending_count,
            "entreprises": entreprises,
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
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    response = templates.TemplateResponse(
        request,
        "pages/dashboard_entreprise/entreprise_overview.html",
        {"user": user, "active_page": "overview", "notifications_count": 0},
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
        except Exception:
            n_creates = n_inserts = 0
        meta = {"creates": n_creates, "inserts": n_inserts}

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
            sources_data.append({
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
            })
        else:
            dernier = imports[0] if imports else None
            meta = {}
            if dernier and dernier.meta_json:
                try:
                    meta = json.loads(dernier.meta_json)
                except Exception:
                    meta = {}
            sources_data.append({
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
            })

    def _fmt_size(n: int | None) -> str:
        if not n:
            return "—"
        if n < 1024:
            return f"{n} o"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} Ko"
        return f"{n / (1024 * 1024):.1f} Mo"

    for s in sources_data:
        s["derniere_date_fmt"] = s["derniere_date"].strftime("%d/%m/%Y %H:%M") if s["derniere_date"] else "—"
        if s["kind"] == "file":
            s["taille_fmt"] = _fmt_size(s.get("taille_octets"))

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
                    .filter(ConfigurationAnalyse.id_import.in_(import_ids)).all()
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
