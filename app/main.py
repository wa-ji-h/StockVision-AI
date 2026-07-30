import os
from fastapi import FastAPI, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.config import settings
from app.database.connection import get_db
from app.dependencies import NotAuthenticated
from app.routes import pages, auth, oauth, dashboard, password_reset

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # = dossier "app/"

app = FastAPI(title="StockVision AI")

# Required by the OAuth flow to hold the CSRF "state" between the redirect
# to Google/GitHub and the callback.
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY or "dev-only-insecure-key")


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/connexion", status_code=303)


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(dashboard.router)
app.include_router(password_reset.router)