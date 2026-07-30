from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class RegisterEntrepriseRequest(BaseModel):
    role: str = "entreprise"
    nom: str = Field(min_length=2, max_length=150)
    secteur_activite: str = Field(min_length=2, max_length=100)
    email: EmailStr
    telephone: str | None = None
    # Pas de mot de passe à l'inscription : l'entreprise en reçoit un par
    # email (lien de réinitialisation à usage unique) une fois approuvée.


class RegisterAdminRequest(BaseModel):
    role: str = "administrateur"
    nom: str = Field(min_length=2, max_length=100)
    email: EmailStr
    admin_code: str = Field(min_length=4)
    password: str = Field(min_length=8)
    password_confirm: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str


class MessageResponse(BaseModel):
    message: str
    detail: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)
    password_confirm: str = Field(min_length=8)
