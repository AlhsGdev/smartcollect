import os
import json
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# CONFIGURATION GLOBALE & SMTP GMAIL
# ==========================================
GMAIL_USER = os.getenv("GMAIL_USER", "votre_adresse@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "votre_mot_de_passe_application_16_caracteres")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./licenses.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 300

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ==========================================
# MODÈLES DE BASE DE DONNÉES
# ==========================================
class LicenseKey(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(32), unique=True, index=True, nullable=False)
    assigned_to_email = Column(String(255), nullable=False, index=True)
    first_name = Column(String(100), default="", nullable=True)
    last_name = Column(String(100), default="", nullable=True)
    organization = Column(String(150), default="", nullable=True)
    is_active = Column(Boolean, default=True)
    device_uuid = Column(Text, default="[]")
    max_devices = Column(Integer, default=1)
    duration_days = Column(Integer, default=30)
    duration_hours = Column(Integer, default=0)
    duration_minutes = Column(Integer, default=0)
    created_at = Column(DateTime, default=get_utc_now)
    activated_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)


class AppNews(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    content = Column(Text, default="", nullable=True)
    category = Column(String(50), default="news") # 'update', 'tip', 'news', 'alert'
    version = Column(String(50), nullable=True)
    download_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)


Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# INITIALISATION FASTAPI & CORS
# ==========================================
app = FastAPI(title="SmartCollect License & News Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# SCHÉMAS PYDANTIC
# ==========================================
class FlutterVerifyRequest(BaseModel):
    key: str
    device_id: str
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    organization: Optional[str] = ""

class SelfRegisterEmailRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    organization: Optional[str] = ""

class CreateLicenseGUIRequest(BaseModel):
    email: EmailStr
    duration_val: int = 1
    duration_unit: str = "Mois"
    max_devices: int = 1

class NewsCreateRequest(BaseModel):
    title: str
    summary: str
    content: Optional[str] = ""
    category: str = "news"
    version: Optional[str] = None
    download_url: Optional[str] = None

# ==========================================
# SERVICE D'ENVOI D'EMAIL (SMTP GMAIL)
# ==========================================
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

# ==========================================
# ROUTES PUBLIQUES
# ==========================================
@app.get("/")
def read_root():
    return {"status": "online", "service": "SmartCollect License API (FastAPI)"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# ==========================================
# ROUTE DEMANDE DE CLÉ PAR EMAIL (GMAIL SMTP)
# ==========================================
@app.post("/api/license/request-key")
def request_license_key(req: SelfRegisterEmailRequest, db: Session = Depends(get_db)):
    clean_email = req.email.lower().strip()

    existing_lic = db.query(LicenseKey).filter(LicenseKey.assigned_to_email == clean_email).first()
    if existing_lic:
        sent = send_license_email(clean_email, existing_lic.key, "30 Jours", existing_lic.max_devices or 1)
        return {
            "status": "success",
            "message": "Une clé existe déjà pour cet e-mail. Elle vient de vous être renvoyée !",
            "email_sent": sent
        }

    part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
    license_key = f"{part1}-{part2}-{part3}-{part4}"

    now = get_utc_now()
    new_lic = LicenseKey(
        key=license_key,
        assigned_to_email=clean_email,
        first_name=req.first_name.strip(),
        last_name=req.last_name.strip(),
        organization=req.organization.strip() if req.organization else "",
        is_active=True,
        device_uuid="[]",
        max_devices=1,
        duration_days=30,
        created_at=now
    )
    db.add(new_lic)
    db.commit()
    db.refresh(new_lic)

    email_sent = send_license_email(clean_email, license_key, "30 Jours", 1)

    if not email_sent:
        raise HTTPException(status_code=500, detail="Erreur lors de l'envoi de l'e-mail via Gmail.")

    return {
        "status": "success",
        "message": "Clé générée et envoyée par e-mail avec succès !"
    }

# ==========================================
# GESTION DES ACTUALITÉS / ASTUCES / MAJ
# ==========================================
@app.get("/api/news")
def get_all_news(db: Session = Depends(get_db)):
    news_items = db.query(AppNews).order_by(AppNews.id.desc()).all()
    results = []
    for n in news_items:
        results.append({
            "id": str(n.id),
            "title": n.title,
            "summary": n.summary,
            "content": n.content or "",
            "category": n.category or "news",
            "version": n.version,
            "download_url": n.download_url,
            "created_at": n.created_at.isoformat() if n.created_at else get_utc_now().isoformat()
        })
    return results

@app.post("/api/admin/news/create")
def create_admin_news(req: NewsCreateRequest, db: Session = Depends(get_db)):
    new_article = AppNews(
        title=req.title.strip(),
        summary=req.summary.strip(),
        content=req.content.strip() if req.content else "",
        category=req.category,
        version=req.version.strip() if req.version else None,
        download_url=req.download_url.strip() if req.download_url else None,
        created_at=get_utc_now()
    )
    db.add(new_article)
    db.commit()
    db.refresh(new_article)

    return {
        "status": "success",
        "id": new_article.id,
        "title": new_article.title
    }

@app.delete("/api/admin/news/{news_id}")
def delete_admin_news(news_id: int, db: Session = Depends(get_db)):
    item = db.query(AppNews).filter(AppNews.id == news_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Article introuvable.")
    db.delete(item)
    db.commit()
    return {"status": "success", "message": "Actualité supprimée."}

# ==========================================
# ROUTE ACTIVATION FLUTTER
# ==========================================
@app.post("/api/license/verify")
def verify_or_activate_flutter(req: FlutterVerifyRequest, db: Session = Depends(get_db)):
    clean_key = req.key.strip().upper()
    license_entry = db.query(LicenseKey).filter(LicenseKey.key == clean_key).first()

    if not license_entry:
        raise HTTPException(status_code=404, detail="Clé de licence introuvable.")

    if not license_entry.is_active or license_entry.device_uuid == "REVOKED":
        raise HTTPException(status_code=403, detail="Cette licence a été désactivée ou révoquée.")

    now = get_utc_now()
    if license_entry.expires_at and now > license_entry.expires_at:
        raise HTTPException(status_code=403, detail="Cette licence a expiré. Veuillez renouveler votre formule.")

    if req.first_name and req.first_name.strip():
        license_entry.first_name = req.first_name.strip()
    if req.last_name and req.last_name.strip():
        license_entry.last_name = req.last_name.strip()
    if req.organization and req.organization.strip():
        license_entry.organization = req.organization.strip()

    if not license_entry.activated_at:
        license_entry.activated_at = now
        total_duration = timedelta(
            days=license_entry.duration_days or 0,
            hours=license_entry.duration_hours or 0,
            minutes=license_entry.duration_minutes or 0
        )
        if total_duration.total_seconds() <= 0:
            total_duration = timedelta(days=30)
        license_entry.expires_at = now + total_duration

    try:
        devices: List[str] = json.loads(license_entry.device_uuid or "[]")
    except Exception:
        devices = []

    max_dev = license_entry.max_devices or 1
    if req.device_id not in devices:
        if len(devices) >= max_dev:
            raise HTTPException(
                status_code=403,
                detail=f"Limite atteinte ({len(devices)}/{max_dev} appareils autorisés pour cette clé)."
            )
        devices.append(req.device_id)
        license_entry.device_uuid = json.dumps(devices)

    db.commit()

    return {
        "status": "valid",
        "email": license_entry.assigned_to_email,
        "first_name": license_entry.first_name,
        "last_name": license_entry.last_name,
        "organization": license_entry.organization,
        "expires_at": license_entry.expires_at.strftime("%Y-%m-%d %H:%M:%S") if license_entry.expires_at else None,
        "devices_used": len(devices),
        "max_devices": max_dev
    }

# ==========================================
# ROUTES ADMINISTRATION LICENCES GUI
# ==========================================
@app.get("/api/admin/licenses")
def get_admin_licenses(db: Session = Depends(get_db)):
    licenses = db.query(LicenseKey).order_by(LicenseKey.id.desc()).all()
    results = []

    for item in licenses:
        try:
            dev_list = json.loads(item.device_uuid or "[]") if item.device_uuid != "REVOKED" else []
        except Exception:
            dev_list = []

        full_name = f"{item.first_name or ''} {item.last_name or ''}".strip()

        results.append({
            "id": item.id,
            "key": item.key,
            "email": item.assigned_to_email,
            "user_name": full_name if full_name else "Non activé",
            "organization": item.organization if item.organization else "—",
            "used_devices": len(dev_list),
            "max_devices": item.max_devices or 1,
            "is_active": item.is_active and item.device_uuid != "REVOKED",
            "created_at": item.created_at.isoformat() if item.created_at else "",
            "expires_at": item.expires_at.isoformat() if item.expires_at else None
        })

    return results

@app.post("/api/admin/licenses/create")
def create_admin_license(req: CreateLicenseGUIRequest, db: Session = Depends(get_db)):
    part1 = secrets.token_hex(2).upper()
    part2 = secrets.token_hex(2).upper()
    part3 = secrets.token_hex(2).upper()
    part4 = secrets.token_hex(2).upper()
    license_key = f"{part1}-{part2}-{part3}-{part4}"

    days, hours, minutes = 0, 0, 0
    unit = req.duration_unit.lower()
    if "mois" in unit:
        days = req.duration_val * 30
    elif "ans" in unit or "an" in unit:
        days = req.duration_val * 365
    elif "jour" in unit:
        days = req.duration_val
    elif "heure" in unit:
        hours = req.duration_val
    elif "minute" in unit:
        minutes = req.duration_val
    else:
        days = req.duration_val

    duration_str = f"{req.duration_val} {req.duration_unit}"

    new_lic = LicenseKey(
        key=license_key,
        assigned_to_email=req.email.lower().strip(),
        is_active=True,
        device_uuid="[]",
        max_devices=req.max_devices,
        duration_days=days,
        duration_hours=hours,
        duration_minutes=minutes,
        created_at=get_utc_now()
    )
    db.add(new_lic)
    db.commit()
    db.refresh(new_lic)

    email_sent = send_license_email(new_lic.assigned_to_email, license_key, duration_str, req.max_devices)

    return {
        "id": new_lic.id,
        "key": license_key,
        "email": new_lic.assigned_to_email,
        "duration": duration_str,
        "email_sent": email_sent
    }

@app.post("/api/admin/licenses/{key}/status")
def toggle_admin_license_status(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    lic.is_active = not lic.is_active
    db.commit()
    return {"status": "success", "is_active": lic.is_active}

@app.post("/api/admin/licenses/{key}/reset-devices")
def reset_admin_license_devices(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    lic.device_uuid = "[]"
    lic.activated_at = None
    lic.expires_at = None
    db.commit()
    return {"status": "success", "message": "Appareils dissociés."}

@app.post("/api/admin/licenses/{key}/resend-email")
def resend_admin_license_email(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    duration_str = f"{lic.duration_days}j {lic.duration_hours}h {lic.duration_minutes}m"
    sent = send_license_email(lic.assigned_to_email, lic.key, duration_str, lic.max_devices or 1)
    if not sent:
        raise HTTPException(status_code=500, detail="Échec lors de l'envoi de l'e-mail via Gmail.")

    return {"status": "success", "message": "E-mail renvoyé avec succès."}

@app.delete("/api/admin/licenses/{key}")
def delete_admin_license(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    db.delete(lic)
    db.commit()
    return {"status": "success", "message": "Licence supprimée définitivement."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
