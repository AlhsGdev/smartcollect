import os
import json
import uuid
import datetime
import requests
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Integer, Text
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
    max_devices = Column(Integer, default=1)
    devices_list = Column(Text, default="[]")  # Liste JSON des UUIDs
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
def send_license_email_task(to_email: str, license_key: str, duration_str: str, max_dev: int):
    if not RESEND_API_KEY or "votre_cle" in RESEND_API_KEY:
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
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px;">
        <div style="background-color: #ffffff; max-width: 520px; margin: auto; padding: 30px; border-radius: 16px; border: 1px solid #e2e8f0;">
            <h2 style="color: #1e293b; text-align: center; margin-top: 0;">SmartCollect — Clé d'Activation</h2>
            <p style="color: #334155; font-size: 15px;">Bonjour,</p>
            <p style="color: #334155; font-size: 14px;">Voici votre clé d'activation officielle pour l'application <b>SmartCollect</b> :</p>
            
            <div style="background-color: #eef2ff; border: 2px solid #6366f1; border-radius: 12px; padding: 18px; text-align: center; margin: 24px 0;">
                <span style="font-size: 24px; font-weight: 800; color: #4338ca; letter-spacing: 2px;">{license_key}</span>
            </div>
            
            <div style="color: #475569; font-size: 14px; line-height: 1.8;">
                • <b>Durée de validité :</b> {duration_str}<br>
                • <b>Appareils autorisés :</b> jusqu'à {max_dev} appareil(s)<br>
                • <i>Le décompte commence dès votre première activation.</i>
            </div>
            
            <div style="text-align: center; color: #94a3b8; font-size: 12px; margin-top: 28px;">
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
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur envoi Resend: {e}")

# ----------------- MODÈLES PYDANTIC -----------------
class LicenseCreateRequest(BaseModel):
    email: str
    duration_val: int = 1
    duration_unit: str = "Mois"  # "Jours", "Mois", "Ans"
    max_devices: int = 1

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
    max_devices: int
    used_devices: int
    is_active: bool
    created_at: datetime.datetime
    expires_at: Optional[datetime.datetime]

# ----------------- FASTAPI APP -----------------
app = FastAPI(title="SmartCollect Cloud License API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "online", "service": "SmartCollect Cloud API"}

@app.get("/api/admin/licenses")
def get_all_licenses(db: Session = Depends(get_db)):
    rows = db.query(LicenseDB).order_by(LicenseDB.created_at.desc()).all()
    results = []
    for lic in rows:
        devs = json.loads(lic.devices_list or "[]")
        results.append({
            "id": lic.id,
            "key": lic.key,
            "email": lic.email,
            "max_devices": lic.max_devices,
            "used_devices": len(devs),
            "is_active": lic.is_active,
            "created_at": lic.created_at.isoformat(),
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
        })
    return results

@app.post("/api/admin/licenses/create")
def create_license(req: LicenseCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    clean_email = req.email.strip().lower()
    
    part1 = uuid.uuid4().hex[:4].upper()
    part2 = uuid.uuid4().hex[:4].upper()
    generated_key = f"APP-{part1}-{part2}"

    # Calcul de la durée
    days = 0
    if req.duration_unit == "Jours":
        days = req.duration_val
    elif req.duration_unit == "Mois":
        days = req.duration_val * 30
    elif req.duration_unit == "Ans":
        days = req.duration_val * 365
    else:
        days = req.duration_val

    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=days)

    lic = LicenseDB(
        key=generated_key,
        email=clean_email,
        max_devices=max(1, req.max_devices),
        devices_list="[]",
        expires_at=expires_at,
        is_active=True
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)

    duration_label = f"{req.duration_val} {req.duration_unit}"
    background_tasks.add_task(send_license_email_task, clean_email, generated_key, duration_label, lic.max_devices)

    return {
        "id": lic.id,
        "key": lic.key,
        "email": lic.email,
        "max_devices": lic.max_devices,
        "used_devices": 0,
        "expires_at": lic.expires_at.isoformat()
    }

@app.delete("/api/admin/licenses/{license_id}")
def delete_license(license_id: int, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    db.delete(lic)
    db.commit()
    return {"message": "Licence supprimée"}

@app.post("/api/admin/licenses/{license_id}/reset-devices")
def reset_devices(license_id: int, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    lic.devices_list = "[]"
    db.commit()
    return {"message": "Appareils réinitialisés"}

@app.post("/api/admin/licenses/{license_id}/resend-email")
def resend_email_route(license_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    
    remaining = lic.expires_at - datetime.datetime.utcnow() if lic.expires_at else None
    duration_str = f"{max(0, remaining.days)} jours restants" if remaining else "Illimité"
    background_tasks.add_task(send_license_email_task, lic.email, lic.key, duration_str, lic.max_devices)
    return {"message": "Envoi initié"}

# ----------------- FLUTTER ACTIVATION MULTI-APPAREILS -----------------
@app.post("/api/license/activate")
def activate_license(req: LicenseActionRequest, db: Session = Depends(get_db)):
    clean_email = (req.email or "").strip().lower()
    raw_key = req.key or req.license_key or req.cle or ""
    clean_key = raw_key.strip().upper()
    device_id = req.device_uuid or "unknown_device"

    if not clean_email or not clean_key:
        raise HTTPException(status_code=400, detail="E-mail et clé requis.")

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

    # Gestion de la liste des appareils
    devices = json.loads(lic.devices_list or "[]")
    if device_id not in devices:
        if len(devices) >= lic.max_devices:
            raise HTTPException(
                status_code=403,
                detail=f"Limite atteinte ({lic.max_devices} appareil(s) maximum autorisés)."
            )
        devices.append(device_id)
        lic.devices_list = json.dumps(devices)
        db.commit()

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
    device_id = req.device_uuid or "unknown_device"

    lic = db.query(LicenseDB).filter(
        LicenseDB.email.ilike(clean_email),
        LicenseDB.key.ilike(clean_key)
    ).first()

    if not lic or not lic.is_active:
        raise HTTPException(status_code=404, detail="Licence invalide.")

    if lic.expires_at and lic.expires_at < datetime.datetime.utcnow():
        raise HTTPException(status_code=403, detail="Licence expirée.")

    devices = json.loads(lic.devices_list or "[]")
    if device_id not in devices and len(devices) >= lic.max_devices:
        raise HTTPException(status_code=403, detail="Appareil non autorisé.")

    return {
        "valid": True,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }
