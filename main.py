import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuration Gmail SMTP
GMAIL_USER = os.getenv("GMAIL_USER", "votre_adresse@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "votre_mot_de_passe_application_16_caracteres")

def send_license_email(recipient_email: str, license_key: str, duration_str: str, max_dev: int) -> bool:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Votre Clé d'Activation SmartCollect ({duration_str})"
    msg["From"] = f"SmartCollect <{GMAIL_USER}>"
    msg["To"] = recipient_email

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #ffffff;">
        <h2 style="color: #4f46e5; text-align: center;">SmartCollect — Clé d'Activation</h2>
        <p>Bonjour,</p>
        <p>Voici votre clé d'activation officielle pour l'application <strong>SmartCollect</strong> :</p>
        <div style="background-color: #f1f5f9; border-left: 5px solid #4f46e5; padding: 18px; text-align: center; margin: 20px 0; border-radius: 6px;">
            <span style="font-size: 24px; font-weight: bold; letter-spacing: 2px; color: #0f172a; font-family: monospace;">{license_key}</span>
        </div>
        <p>• <strong>Durée de validité :</strong> {duration_str}<br>
           • <strong>Appareils autorisés :</strong> jusqu'à {max_dev} appareil(s)</p>
        <p style="font-size: 12px; color: #94a3b8; text-align: center;">Le décompte commence dès votre première activation dans l'application.</p>
    </div>
    """
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, [recipient_email], msg.as_string())
        return True
    except Exception as e:
        print(f"Erreur SMTP Gmail: {e}")
        return False
