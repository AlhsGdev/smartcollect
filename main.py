import os
import uuid
import datetime
import requests
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ----------------- CONFIGURATION RESEND -----------------
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_SENDER = "SmartCollect <onboarding@resend.dev>"

# ----------------- BASE DE DONNÉES -----------------
DATABASE_URL = "sqlite:///./licenses.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class LicenseDB(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    key = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, index=True, nullable=False)
    device_uuid = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------- FONCTION D'ENVOI D'EMAIL EN ARRIÈRE-PLAN -----------------
def send_license_email_task(to_email: str, license_key: str, duration_str: str):
    if not RESEND_API_KEY or "votre_cle" in RESEND_API_KEY:
        print("Clé RESEND manquante, envoi annulé.")
        return

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 20px;">
        <div style="background-color: #ffffff; max-width: 500px; margin: auto; padding: 25px; border-radius: 12px; border: 1px solid #e2e8f0;">
            <h2 style="color: #1e293b; text-align: center;">SmartCollect — Clé d'Activation</h2>
            <p>Bonjour,</p>
            <p>Voici votre clé d'activation officielle pour l'application <b>SmartCollect</b> :</p>
            <div style="background-color: #eef2ff; border: 2px solid #6366f1; border-radius: 8px; padding: 15px; text-align: center; margin: 20px 0;">
                <span style="font-size: 22px; font-weight: bold; color: #4338ca; letter-spacing: 2px;">{license_key}</span>
            </div>
            <p style="color: #475569; font-size: 14px;">
                • <b>Durée :</b> {duration_str}<br>
                • <b>Appareil :</b> 1 terminal actif
            </p>
        </div>
    </body>
    </html>
    """

    payload = {
        "from": RESEND_SENDER,
        "to": [to_email],
        "subject": f"Votre Clé d'Activation SmartCollect ({duration_str})",
        "html": html_content
    }

    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur envoi Resend: {e}")

# ----------------- MODÈLES PYDANTIC -----------------
class LicenseCreateRequest(BaseModel):
    email: str
    duration_days: int = 30
    duration_hours: int = 0
    duration_minutes: int = 0

class LicenseActionRequest(BaseModel):
    email: Optional[str] = None
    key: Optional[str] = None
    license_key: Optional[str] = None
    cle: Optional[str] = None
    device_uuid: Optional[str] = None

class LicenseResponse(BaseModel):
    id: int
    key: str
    email: str
    device_uuid: Optional[str]
    is_active: bool
    created_at: datetime.datetime
    expires_at: Optional[datetime.datetime]

    class Config:
        from_attributes = True

# ----------------- FASTAPI APP -----------------
app = FastAPI(title="SmartCollect License API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "online", "service": "SmartCollect License API (FastAPI)"}

@app.get("/api/admin/licenses", response_model=List[LicenseResponse])
def get_all_licenses(db: Session = Depends(get_db)):
    return db.query(LicenseDB).order_by(LicenseDB.created_at.desc()).all()

@app.post("/api/admin/licenses/create", response_model=LicenseResponse)
def create_license(req: LicenseCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    clean_email = req.email.strip().lower()
    
    part1 = uuid.uuid4().hex[:4].upper()
    part2 = uuid.uuid4().hex[:4].upper()
    generated_key = f"APP-{part1}-{part2}"

    total_delta = datetime.timedelta(
        days=req.duration_days,
        hours=req.duration_hours,
        minutes=req.duration_minutes
    )
    expires_at = datetime.datetime.utcnow() + total_delta

    lic = LicenseDB(
        key=generated_key,
        email=clean_email,
        expires_at=expires_at,
        is_active=True
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)

    duration_label = f"{req.duration_days}j {req.duration_hours}h {req.duration_minutes}min"
    # Envoi en arrière-plan sans bloquer la réponse HTTP
    background_tasks.add_task(send_license_email_task, clean_email, generated_key, duration_label)

    return lic

@app.delete("/api/admin/licenses/{license_id}")
def delete_license(license_id: int, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    db.delete(lic)
    db.commit()
    return {"message": "Licence supprimée avec succès"}

@app.post("/api/admin/licenses/{license_id}/resend-email")
def resend_email_route(license_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    
    duration_str = "Valide"
    if lic.expires_at:
        remaining = lic.expires_at - datetime.datetime.utcnow()
        duration_str = f"{max(0, remaining.days)} jours restants"

    background_tasks.add_task(send_license_email_task, lic.email, lic.key, duration_str)
    return {"message": "Envoi de l'e-mail initié"}

# ----------------- ACTIVATION FLUTTER -----------------
@app.post("/api/license/activate")
def activate_license(req: LicenseActionRequest, db: Session = Depends(get_db)):
    clean_email = (req.email or "").strip().lower()
    raw_key = req.key or req.license_key or req.cle or ""
    clean_key = raw_key.strip().upper()

    if not clean_email or not clean_key:
        raise HTTPException(status_code=400, detail="E-mail et clé d'activation requis.")

    lic = db.query(LicenseDB).filter(
        LicenseDB.email.ilike(clean_email),
        LicenseDB.key.ilike(clean_key)
    ).first()

    if not lic:
        raise HTTPException(status_code=404, detail="Clé de licence introuvable.")

    if not lic.is_active:
        raise HTTPException(status_code=403, detail="Cette licence a été révoquée.")

    if lic.expires_at and lic.expires_at < datetime.datetime.utcnow():
        raise HTTPException(status_code=403, detail="Cette licence a expiré.")

    if lic.device_uuid is None:
        lic.device_uuid = req.device_uuid
        db.commit()
        db.refresh(lic)

    return {
        "success": True,
        "message": "Licence activée avec succès !",
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }

@app.post("/api/license/verify")
def verify_license(req: LicenseActionRequest, db: Session = Depends(get_db)):
    clean_email = (req.email or "").strip().lower()
    raw_key = req.key or req.license_key or req.cle or ""
    clean_key = raw_key.strip().upper()

    lic = db.query(LicenseDB).filter(
        LicenseDB.email.ilike(clean_email),
        LicenseDB.key.ilike(clean_key)
    ).first()

    if not lic or not lic.is_active:
        raise HTTPException(status_code=404, detail="Licence invalide ou révoquée.")

    if lic.expires_at and lic.expires_at < datetime.datetime.utcnow():
        raise HTTPException(status_code=403, detail="Licence expirée.")

    return {
        "valid": True,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }
