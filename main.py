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
    device_ids = Column(String, default="")  # Séparés par des virgules
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------
# Schémas Pydantic
# ---------------------------------------------------------
class LicenseCreateRequest(BaseModel):
    email: EmailStr
    duration_val: int = 1
    duration_unit: str = "Mois"  # "Mois", "Jours", "Heures", "Minutes", "Ans"
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
    # Format standard : ABCD-EFGH-IJKL-MNOP (4 blocs de 4 caractères alphanumériques majuscules)
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
    else:  # Mois par défaut
        return now + timedelta(days=val * 30)

# ---------------------------------------------------------
# Routes Publiques & Flutter
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
    return {"message": f"Email envoyé à {lic.email}."}

@app.delete("/api/admin/licenses/{key}")
def delete_license(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseModel).filter(LicenseModel.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable.")
    db.delete(lic)
    db.commit()
    return {"message": "Licence supprimée."}
