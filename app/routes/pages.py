import os
import time
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

# app/routes/pages.py → on remonte de "routes" vers "app"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Cache-buster for /static assets: changes on every server restart, so
# browsers stop serving a stale style.css/auth.js after we edit them.
templates.env.globals["asset_version"] = str(int(time.time()))

router = APIRouter()

@router.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "pages/home/home.html")

@router.get("/fonctionnalites")
def features(request: Request):
    return templates.TemplateResponse(request, "pages/home/features.html")

@router.get("/services")
def services(request: Request):
    return templates.TemplateResponse(request, "pages/home/services.html")

@router.get("/a-propos")
def about(request: Request):
    return templates.TemplateResponse(request, "pages/home/about.html")

@router.get("/contact")
def contact(request: Request):
    return templates.TemplateResponse(request, "pages/home/contact.html")

@router.get("/connexion")
def signin(request: Request):
    oauth_error = request.query_params.get("oauth_error", "")
    oauth_pending = request.query_params.get("oauth_pending", "")
    return templates.TemplateResponse(request, "pages/home/signin.html", {
        "oauth_error": oauth_error,
        "oauth_pending": oauth_pending,
    })

@router.get("/inscription")
def signup(request: Request):
    return templates.TemplateResponse(request, "pages/home/signup.html")