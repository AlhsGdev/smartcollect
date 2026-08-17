import os
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ==========================================
# CONFIGURATION & ENVIRONNEMENT
# ==========================================
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "onboarding@resend.dev")
MAX_DEVICES = 3

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./licenses.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ==========================================
# MODÈLE DE BASE DE DONNÉES
# ==========================================
class LicenseKey(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    assigned_to_email = Column(String, nullable=False, index=True)
    is_active = Column(Boolean, default=False)
    device_uuid = Column(Text, default="[]")
    duration_days = Column(Integer, default=30)
    duration_hours = Column(Integer, default=0)
    duration_minutes = Column(Integer, default=0)
    created_at = Column(DateTime, default=get_utc_now)
    activated_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(title="SmartCollect License Server")

# ==========================================
# SCHÉMAS PYDANTIC
# ==========================================
class GenerateKeyRequest(BaseModel):
    email: EmailStr
    days: int = 30
    hours: int = 0
    minutes: int = 0

class LicenseAuthRequest(BaseModel):
    email: EmailStr
    key: str
    device_uuid: str

class UpdateLicenseRequest(BaseModel):
    email: Optional[EmailStr] = None
    days: Optional[int] = None
    hours: Optional[int] = None
    minutes: Optional[int] = None

class AdminActionRequest(BaseModel):
    action: str

# ==========================================
# SERVICE D'ENVOI D'E-MAILS (RESEND)
# ==========================================
def send_license_email(recipient_email: str, license_key: str, days: int, hours: int, minutes: int) -> bool:
    if not RESEND_API_KEY:
        return False

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    duration_str = f"{days}j {hours}h {minutes}min"
    payload = {
        "from": f"SmartCollect <{SENDER_EMAIL}>",
        "to": [recipient_email],
        "subject": f"Votre Clé d'Activation SmartCollect ({duration_str})",
        "html": f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #ffffff;">
            <h2 style="color: #4f46e5; text-align: center;">SmartCollect — Clé d'Activation</h2>
            <p>Bonjour,</p>
            <p>Voici votre clé d'activation officielle pour l'application <strong>SmartCollect</strong> :</p>
            <div style="background-color: #f1f5f9; border-left: 5px solid #4f46e5; padding: 18px; text-align: center; margin: 20px 0; border-radius: 6px;">
                <span style="font-size: 26px; font-weight: bold; letter-spacing: 2px; color: #0f172a;">{license_key}</span>
            </div>
            <p>• <strong>Durée de validité :</strong> {duration_str}<br>
               • <strong>Appareils autorisés :</strong> jusqu'à {MAX_DEVICES} appareils</p>
            <p style="font-size: 12px; color: #94a3b8; text-align: center;">Le décompte commence dès votre première activation.</p>
        </div>
        """
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        return response.status_code in [200, 201]
    except Exception:
        return False

# ==========================================
# ROUTES PUBLIQUES & CLIENT
# ==========================================
@app.get("/")
def read_root():
    return {"status": "online", "service": "SmartCollect License API"}

@app.post("/api/license/activate")
def activate_license(req: LicenseAuthRequest, db: Session = Depends(get_db)):
    license_entry = db.query(LicenseKey).filter(LicenseKey.key == req.key.strip()).first()

    if not license_entry:
        raise HTTPException(status_code=404, detail="Clé de licence introuvable.")

    if license_entry.assigned_to_email.lower().strip() != req.email.lower().strip():
        raise HTTPException(status_code=403, detail="Cette clé n'est pas assignée à cette adresse e-mail.")

    if license_entry.device_uuid == "REVOKED":
        raise HTTPException(status_code=403, detail="Cette licence a été révoquée par l'administrateur.")

    now = get_utc_now()

    if license_entry.expires_at and now > license_entry.expires_at:
        raise HTTPException(status_code=403, detail="Cette licence a expiré.")

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

    if req.device_uuid in devices:
        db.commit()
        return {
            "status": "success",
            "message": f"Appareil déjà activé ({len(devices)}/{MAX_DEVICES} appareils).",
            "expires_at": license_entry.expires_at.strftime("%Y-%m-%d %H:%M:%S")
        }

    if len(devices) >= MAX_DEVICES:
        raise HTTPException(
            status_code=403,
            detail=f"Limite atteinte : Cette clé est déjà utilisée sur {MAX_DEVICES} appareils."
        )

    devices.append(req.device_uuid)
    license_entry.device_uuid = json.dumps(devices)
    license_entry.is_active = True
    db.commit()

    return {
        "status": "success",
        "message": f"Activation réussie ({len(devices)}/{MAX_DEVICES} appareils).",
        "expires_at": license_entry.expires_at.strftime("%Y-%m-%d %H:%M:%S")
    }

@app.post("/api/license/verify")
def verify_license(req: LicenseAuthRequest, db: Session = Depends(get_db)):
    license_entry = db.query(LicenseKey).filter(LicenseKey.key == req.key.strip()).first()

    if not license_entry:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    if license_entry.device_uuid == "REVOKED":
        raise HTTPException(status_code=403, detail="Licence révoquée par l'administrateur.")

    if license_entry.assigned_to_email.lower().strip() != req.email.lower().strip():
        raise HTTPException(status_code=403, detail="E-mail non concordant.")

    now = get_utc_now()
    if license_entry.expires_at and now > license_entry.expires_at:
        raise HTTPException(status_code=403, detail="Cette licence a expiré.")

    try:
        devices: List[str] = json.loads(license_entry.device_uuid or "[]")
    except Exception:
        devices = []

    if req.device_uuid not in devices or not license_entry.is_active:
        raise HTTPException(status_code=403, detail="Cet appareil n'est pas autorisé.")

    return {
        "status": "valid",
        "message": "Licence active.",
        "expires_at": license_entry.expires_at.strftime("%Y-%m-%d %H:%M:%S") if license_entry.expires_at else None,
        "active_devices_count": len(devices),
        "max_devices": MAX_DEVICES
    }

# ==========================================
# ROUTES ADMINISTRATEUR (DASHBOARD GUI)
# ==========================================
@app.get("/api/admin/licenses")
def get_all_licenses(db: Session = Depends(get_db)):
    licenses = db.query(LicenseKey).order_by(LicenseKey.id.desc()).all()
    now = get_utc_now()
    results = []

    for item in licenses:
        raw_devices = item.device_uuid or "[]"
        is_expired = False
        if item.expires_at and now > item.expires_at:
            is_expired = True

        if raw_devices == "REVOKED":
            device_status = "0/3 (Révoqué)"
            status_text = "🔴 Révoquée"
        elif is_expired:
            device_status = "Expiré"
            status_text = "⌛ Expirée"
        else:
            try:
                dev_list = json.loads(raw_devices) if raw_devices.startswith("[") else [raw_devices]
                device_status = f"{len(dev_list)} / {MAX_DEVICES}"
            except Exception:
                device_status = f"0 / {MAX_DEVICES}"

            status_text = "🟢 Active" if item.is_active else "🟡 En attente"

        results.append({
            "id": item.id,
            "key": item.key,
            "assigned_to_email": item.assigned_to_email,
            "status_text": status_text,
            "device_status": device_status,
            "duration_days": item.duration_days,
            "duration_hours": item.duration_hours,
            "duration_minutes": item.duration_minutes,
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "—",
            "expires_at": item.expires_at.strftime("%Y-%m-%d %H:%M:%S") if item.expires_at else "Non activé"
        })

    return {"licenses": results}

@app.post("/api/admin/generate-key")
def generate_key(req: GenerateKeyRequest, db: Session = Depends(get_db)):
    part1 = secrets.token_hex(2).upper()
    part2 = secrets.token_hex(2).upper()
    license_key = f"APP-{part1}-{part2}"

    new_license = LicenseKey(
        key=license_key,
        assigned_to_email=req.email.lower().strip(),
        is_active=False,
        device_uuid="[]",
        duration_days=req.days,
        duration_hours=req.hours,
        duration_minutes=req.minutes,
        created_at=get_utc_now()
    )
    db.add(new_license)
    db.commit()
    db.refresh(new_license)

    email_sent = send_license_email(req.email.lower().strip(), license_key, req.days, req.hours, req.minutes)
    return {
        "status": "success",
        "key": license_key,
        "email": req.email,
        "duration": f"{req.days}j {req.hours}h {req.minutes}min",
        "email_sent": email_sent
    }

@app.put("/api/admin/license/{key_id}")
def update_license(key_id: int, req: UpdateLicenseRequest, db: Session = Depends(get_db)):
    license_entry = db.query(LicenseKey).filter(LicenseKey.id == key_id).first()
    if not license_entry:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    if req.email:
        license_entry.assigned_to_email = req.email.lower().strip()

    duration_modified = False
    if req.days is not None:
        license_entry.duration_days = req.days
        duration_modified = True
    if req.hours is not None:
        license_entry.duration_hours = req.hours
        duration_modified = True
    if req.minutes is not None:
        license_entry.duration_minutes = req.minutes
        duration_modified = True

    if duration_modified and license_entry.activated_at:
        new_duration = timedelta(
            days=license_entry.duration_days or 0,
            hours=license_entry.duration_hours or 0,
            minutes=license_entry.duration_minutes or 0
        )
        license_entry.expires_at = license_entry.activated_at + new_duration

    db.commit()
    return {"status": "success", "message": "Licence mise à jour avec succès."}

@app.post("/api/admin/license/{key_id}/action")
def perform_license_action(key_id: int, req: AdminActionRequest, db: Session = Depends(get_db)):
    license_entry = db.query(LicenseKey).filter(LicenseKey.id == key_id).first()
    if not license_entry:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    if req.action == "revoke":
        license_entry.device_uuid = "REVOKED"
        license_entry.is_active = False
    elif req.action == "reset":
        license_entry.device_uuid = "[]"
        license_entry.is_active = False
        license_entry.activated_at = None
        license_entry.expires_at = None
    else:
        raise HTTPException(status_code=400, detail="Action invalide. Utilisez 'revoke' ou 'reset'.")

    db.commit()
    return {"status": "success", "message": f"Action '{req.action}' exécutée."}

@app.post("/api/admin/license/{key_id}/resend")
def resend_license_email(key_id: int, db: Session = Depends(get_db)):
    license_entry = db.query(LicenseKey).filter(LicenseKey.id == key_id).first()
    if not license_entry:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    sent = send_license_email(
        recipient_email=license_entry.assigned_to_email,
        license_key=license_entry.key,
        days=license_entry.duration_days or 30,
        hours=license_entry.duration_hours or 0,
        minutes=license_entry.duration_minutes or 0
    )

    if not sent:
        raise HTTPException(status_code=500, detail="Échec de l'envoi de l'e-mail.")

    return {"status": "success", "message": "E-mail renvoyé avec succès."}

@app.delete("/api/admin/license/{key_id}")
def delete_license(key_id: int, db: Session = Depends(get_db)):
    license_entry = db.query(LicenseKey).filter(LicenseKey.id == key_id).first()
    if not license_entry:
        raise HTTPException(status_code=404, detail="Licence introuvable.")

    db.delete(license_entry)
    db.commit()
    return {"status": "success", "message": "Licence supprimée avec succès."}
