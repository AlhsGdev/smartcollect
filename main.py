import os
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, List

import requests
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import Column, Integer, String, Boolean, DateTime, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ---------------------------------------------------------
# Configuration Base de données (SQLite Local & Render)
# ---------------------------------------------------------
DB_FILE = os.environ.get("DB_PATH", "licenses.db")
DATABASE_URL = f"sqlite:///{DB_FILE}"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------
# Modèle SQLAlchemy
# ---------------------------------------------------------
class LicenseModel(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    max_devices = Column(Integer, default=1)
    used_devices = Column(Integer, default=0)
    device_ids = Column(String, default="")
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------
# Schémas Pydantic
# ---------------------------------------------------------
class LicenseCreateRequest(BaseModel):
    email: EmailStr
    duration_val: int = 1
    duration_unit: str = "Mois"
    max_devices: int = 1

class LicenseVerifyRequest(BaseModel):
    key: str
    device_id: str

class LicenseResponse(BaseModel):
    id: int
    key: str
    email: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    max_devices: int
    used_devices: int
    is_active: bool

    class Config:
        from_attributes = True

# ---------------------------------------------------------
# Initialisation FastAPI
# ---------------------------------------------------------
app = FastAPI(title="SmartCollect License API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_license_key() -> str:
    chars = string.ascii_uppercase + string.digits
    blocks = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(4)]
    return "-".join(blocks)

def calculate_expiration(val: int, unit: str) -> datetime:
    now = datetime.utcnow()
    unit = unit.lower()
    if "minute" in unit:
        return now + timedelta(minutes=val)
    elif "heure" in unit:
        return now + timedelta(hours=val)
    elif "jour" in unit:
        return now + timedelta(days=val)
    elif "an" in unit:
        return now + timedelta(days=val * 365)
    else:
        return now + timedelta(days=val * 30)

def send_license_email(to_email: str, license_key: str, expires_at: Optional[datetime], max_devices: int) -> bool:
    if not RESEND_API_KEY:
        print("ATTENTION: RESEND_API_KEY non configurée.")
        return False

    exp_str = expires_at.strftime("%d/%m/%Y à %H:%M UTC") if expires_at else "Illimitée"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 24px; }}
        .container {{ max-width: 520px; margin: 0 auto; background: #1e293b; border-radius: 12px; border: 1px solid #334155; padding: 32px; }}
        .title {{ font-size: 22px; font-weight: bold; color: #ffffff; text-align: center; margin-bottom: 8px; }}
        .subtitle {{ font-size: 14px; color: #94a3b8; text-align: center; margin-bottom: 24px; }}
        .key-box {{ background: #0f172a; border: 2px dashed #6366f1; border-radius: 8px; padding: 18px; text-align: center; font-size: 22px; font-weight: bold; letter-spacing: 2px; color: #818cf8; font-family: monospace; margin-bottom: 24px; }}
        .info-row {{ display: flex; justify-content: space-between; font-size: 13px; color: #cbd5e1; border-bottom: 1px solid #334155; padding: 10px 0; }}
        .footer {{ font-size: 12px; color: #64748b; text-align: center; margin-top: 24px; }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="title">Votre Clé d'Activation SmartCollect</div>
        <div class="subtitle">Merci pour votre confiance. Voici vos identifiants d'activation :</div>
        
        <div class="key-box">{license_key}</div>

        <div class="info-row">
          <span>Date d'expiration :</span>
          <strong>{exp_str}</strong>
        </div>
        <div class="info-row">
          <span>Appareils autorisés :</span>
          <strong>{max_devices} appareil(s)</strong>
        </div>

        <div class="footer">
          Collez cette clé directement à l'ouverture de l'application SmartCollect pour débloquer votre accès.
        </div>
      </div>
    </body>
    </html>
    """

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "SmartCollect <onboarding@resend.dev>",
        "to": [to_email],
        "subject": "Votre Clé de Licence SmartCollect",
        "html": html_content
    }

    try:
        res = requests.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=10)
        return res.status_code in [200, 201]
    except Exception as e:
        print(f"Erreur d'envoi d'email : {e}")
        return False

# ---------------------------------------------------------
# Routes Publiques, Health-Check & Flutter
# ---------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "online", "service": "SmartCollect License API"}

@app.post("/api/license/verify")
def verify_license(payload: LicenseVerifyRequest, db: Session = Depends(get_db)):
    clean_key = payload.key.strip().upper()
    lic = db.query(LicenseModel).filter(LicenseModel.key == clean_key).first()
    
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")
    
    if not lic.is_active:
        raise HTTPException(status_code=403, detail="Cette licence a été révoquée.")
    
    if lic.expires_at and datetime.utcnow() > lic.expires_at:
        raise HTTPException(status_code=403, detail="Cette licence a expiré.")
    
    devices = [d.strip() for d in lic.device_ids.split(",") if d.strip()]
    
    if payload.device_id not in devices:
        if len(devices) >= lic.max_devices:
            raise HTTPException(status_code=403, detail="Nombre maximum d'appareils atteint.")
        devices.append(payload.device_id)
        lic.device_ids = ",".join(devices)
        lic.used_devices = len(devices)
        db.commit()
    
    return {
        "valid": True,
        "email": lic.email,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        "max_devices": lic.max_devices,
        "used_devices": lic.used_devices
    }

# ---------------------------------------------------------
# Routes Administration Desktop
# ---------------------------------------------------------
@app.get("/api/admin/licenses", response_model=List[LicenseResponse])
def get_all_licenses(db: Session = Depends(get_db)):
    return db.query(LicenseModel).order_by(LicenseModel.id.desc()).all()

@app.post("/api/admin/licenses/create", response_model=LicenseResponse)
def create_license(payload: LicenseCreateRequest, db: Session = Depends(get_db)):
    key = generate_license_key()
    expires_at = calculate_expiration(payload.duration_val, payload.duration_unit)
    
    lic = LicenseModel(
        key=key,
        email=payload.email,
        expires_at=expires_at,
        max_devices=payload.max_devices,
        used_devices=0,
        device_ids="",
        is_active=True
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)

    # Envoi direct de l'email
    send_license_email(lic.email, lic.key, lic.expires_at, lic.max_devices)

    return lic

@app.post("/api/admin/licenses/{key}/status")
def toggle_license_status(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseModel).filter(LicenseModel.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")
    lic.is_active = not lic.is_active
    db.commit()
    return {"key": lic.key, "is_active": lic.is_active}

@app.post("/api/admin/licenses/{key}/reset-devices")
def reset_devices(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseModel).filter(LicenseModel.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")
    lic.device_ids = ""
    lic.used_devices = 0
    db.commit()
    return {"key": lic.key, "message": "Appareils réinitialisés."}

@app.post("/api/admin/licenses/{key}/resend-email")
def resend_email(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseModel).filter(LicenseModel.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")
    
    sent = send_license_email(lic.email, lic.key, lic.expires_at, lic.max_devices)
    if not sent:
        raise HTTPException(status_code=500, detail="Échec de l'envoi de l'email via Resend.")

    return {"message": f"Email renvoyé avec succès à {lic.email}."}

@app.delete("/api/admin/licenses/{key}")
def delete_license(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseModel).filter(LicenseModel.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")
    db.delete(lic)
    db.commit()
    return {"message": "Licence supprimée."}
