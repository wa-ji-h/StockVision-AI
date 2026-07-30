from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import get_db
from app.services import oauth_service

router = APIRouter(prefix="/api/auth/oauth", tags=["oauth"])

PROVIDERS = {"google", "github"}

oauth = OAuth()

oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

oauth.register(
    name="github",
    client_id=settings.GITHUB_CLIENT_ID,
    client_secret=settings.GITHUB_CLIENT_SECRET,
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "read:user user:email"},
)


def _redirect_home(request: Request, **params: str) -> RedirectResponse:
    base = str(request.base_url).rstrip("/")
    return RedirectResponse(f"{base}/?{urlencode(params)}")


@router.get("/{provider}/login")
async def oauth_login(provider: str, request: Request):
    if provider not in PROVIDERS:
        return _redirect_home(request, oauth_error="Fournisseur inconnu.")

    client = oauth.create_client(provider)
    if not client.client_id or not client.client_secret:
        return _redirect_home(
            request,
            oauth_error=(
                f"Connexion {provider.capitalize()} pas encore configurée côté serveur "
                "(GOOGLE_CLIENT_ID/GITHUB_CLIENT_ID manquants dans .env)."
            ),
        )

    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE_URL}/api/auth/oauth/{provider}/callback"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/{provider}/callback")
async def oauth_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    if provider not in PROVIDERS:
        return _redirect_home(request, oauth_error="Fournisseur inconnu.")

    client = oauth.create_client(provider)

    try:
        token = await client.authorize_access_token(request)
    except Exception:
        return _redirect_home(request, oauth_error="Échec de l'authentification OAuth.")

    email = None
    name = None
    provider_id = None

    if provider == "google":
        profile = token.get("userinfo")
        if not profile:
            profile = await client.userinfo(token=token)
        email = profile.get("email")
        name = profile.get("name")
        provider_id = profile.get("sub")
    else:  # github
        resp = await client.get("user", token=token)
        profile = resp.json()
        provider_id = str(profile.get("id"))
        name = profile.get("name") or profile.get("login")
        email = profile.get("email")
        if not email:
            emails_resp = await client.get("user/emails", token=token)
            emails = emails_resp.json() if emails_resp.status_code == 200 else []
            primary = next((e for e in emails if e.get("primary")), emails[0] if emails else None)
            email = primary["email"] if primary else None

    if not email:
        return _redirect_home(
            request,
            oauth_error="Impossible de récupérer votre email depuis ce fournisseur.",
        )

    try:
        result = oauth_service.login_or_create_oauth_user(
            db, provider=provider, provider_id=provider_id or email, email=email, name=name
        )
    except HTTPException as exc:
        return _redirect_home(request, oauth_error=str(exc.detail))

    # Same session mechanism as the password login: httponly cookie, then
    # straight to the right dashboard (no token ever touches localStorage).
    dashboard = "/dashboard/admin" if result["role"] == "administrateur" else "/dashboard/entreprise"
    base = str(request.base_url).rstrip("/")
    response = RedirectResponse(f"{base}{dashboard}")
    response.set_cookie(
        key="access_token",
        value=result["access_token"],
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return response
