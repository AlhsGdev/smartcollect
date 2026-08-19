import os
import uuid
import datetime
import requests
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ----------------- CONFIGURATION RESEND -----------------
# Vous pouvez définir la variable RESEND_API_KEY sur Fly.io ou la laisser ici
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_SENDER = "SmartCollect <onboarding@resend.dev>"  # Ou votre domaine vérifié

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

# ----------------- FONCTION D'ENVOI D'EMAIL -----------------
def send_license_email(to_email: str, license_key: str, duration_str: str):
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; }}
            .card {{ background-color: #ffffff; max-width: 520px; margin: auto; padding: 30px; border-radius: 16px; border: 1px solid #e2e8f0; }}
            .title {{ font-size: 20px; font-weight: bold; color: #1e293b; text-align: center; }}
            .key-box {{ background-color: #eef2ff; border: 2px solid #6366f1; border-radius: 12px; padding: 18px; text-align: center; margin: 24px 0; }}
            .key-text {{ font-size: 24px; font-weight: 800; color: #4338ca; letter-spacing: 2px; }}
            .details {{ color: #475569; font-size: 14px; line-height: 1.6; }}
            .footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 24px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="title">SmartCollect — Clé d'Activation</div>
            <p style="color: #334155; font-size: 15px; margin-top: 20px;">Bonjour,</p>
            <p style="color: #334155; font-size: 14px;">Voici votre clé d'activation officielle pour l'application <b>SmartCollect</b> :</p>
            
            <div class="key-box">
                <div class="key-text">{license_key}</div>
            </div>
            
            <div class="details">
                • <b>Durée de validité :</b> {duration_str}<br>
                • <b>Appareils autorisés :</b> 1 appareil actif<br>
                • <i>Le décompte commence dès votre première activation sur l'application.</i>
            </div>
            
            <div class="footer">
                SmartCollect Security System • Ne partagez pas cette clé.
            </div>
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
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        return res.status_code in [200, 201]
    except Exception as e:
        print(f"Erreur envoi email: {e}")
        return False

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

# ----------------- APPLICATION FASTAPI -----------------
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

# ----------------- ROUTES ADMINISTRATION -----------------
@app.get("/api/admin/licenses", response_model=List[LicenseResponse])
def get_all_licenses(db: Session = Depends(get_db)):
    return db.query(LicenseDB).order_by(LicenseDB.created_at.desc()).all()

@app.post("/api/admin/licenses/create", response_model=LicenseResponse)
def create_license(req: LicenseCreateRequest, db: Session = Depends(get_db)):
    clean_email = req.email.strip().lower()
    
    # Format de clé identique à vos emails : APP-XXXX-XXXX
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

    # Déclenchement automatique de l'envoi de l'e-mail
    duration_label = f"{req.duration_days}j {req.duration_hours}h {req.duration_minutes}min"
    send_license_email(clean_email, generated_key, duration_label)

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
def resend_email_route(license_id: int, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    
    duration_str = "Valide"
    if lic.expires_at:
        remaining = lic.expires_at - datetime.datetime.utcnow()
        duration_str = f"{max(0, remaining.days)} jours restants"

    success = send_license_email(lic.email, lic.key, duration_str)
    if not success:
        raise HTTPException(status_code=500, detail="Échec lors de l'envoi du mail via Resend")
    return {"message": "E-mail renvoyé avec succès"}

# ----------------- ROUTES APPLICATION FLUTTER -----------------
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

    # Attribution de l'appareil
    if lic.device_uuid is None:
        lic.device_uuid = req.device_uuid
        db.commit()
        db.refresh(lic)
    elif req.device_uuid and lic.device_uuid != req.device_uuid:
        # Enlever la restriction si vous souhaitez autoriser la même clé sur plusieurs postes
        pass

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
