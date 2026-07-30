import smtplib
from email.mime.text import MIMEText

from app.core.config import settings


def send_email(to: str, subject: str, body: str) -> None:
    """Envoie un email texte simple. Si aucun SMTP n'est configuré (dev),
    affiche le contenu dans la console au lieu d'échouer — ça permet de
    tester tout le flux d'approbation/reset sans serveur mail réel."""
    if not settings.SMTP_HOST:
        print(
            "\n===== [email:dev — SMTP non configuré, affiché ici] =====\n"
            f"To: {to}\nSubject: {subject}\n\n{body}\n"
            "==========================================================\n"
        )
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(msg["From"], [to], msg.as_string())
