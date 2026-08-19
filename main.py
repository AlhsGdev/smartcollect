import os
import uuid
import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ----------------- Base de Données -----------------
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

# ----------------- Modèles Pydantic -----------------
class LicenseCreateRequest(BaseModel):
    email: str
    duration_days: int = 30
    duration_hours: int = 0
    duration_minutes: int = 0

class LicenseUpdateRequest(BaseModel):
    email: Optional[str] = None
    duration_days: Optional[int] = None
    duration_hours: Optional[int] = None
    duration_minutes: Optional[int] = None

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

# ----------------- Application FastAPI -----------------
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

# ----------------- Routes Administrateur (PySide6) -----------------
@app.get("/api/admin/licenses", response_model=List[LicenseResponse])
def get_all_licenses(db: Session = Depends(get_db)):
    return db.query(LicenseDB).order_by(LicenseDB.created_at.desc()).all()

@app.post("/api/admin/licenses/create", response_model=LicenseResponse)
def create_license(req: LicenseCreateRequest, db: Session = Depends(get_db)):
    clean_email = req.email.strip().lower()
    
    # Génération clé au format APP-XXXX-XXXX
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
    return lic

@app.put("/api/admin/licenses/{license_id}", response_model=LicenseResponse)
def update_license(license_id: int, req: LicenseUpdateRequest, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")

    if req.email:
        lic.email = req.email.strip().lower()

    if req.duration_days is not None:
        total_delta = datetime.timedelta(
            days=req.duration_days,
            hours=req.duration_hours or 0,
            minutes=req.duration_minutes or 0
        )
        lic.expires_at = datetime.datetime.utcnow() + total_delta

    db.commit()
    db.refresh(lic)
    return lic

@app.delete("/api/admin/licenses/{license_id}")
def delete_license(license_id: int, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    db.delete(lic)
    db.commit()
    return {"message": "Licence supprimée avec succès"}

@app.post("/api/admin/licenses/{license_id}/reset")
def reset_device(license_id: int, db: Session = Depends(get_db)):
    lic = db.query(LicenseDB).filter(LicenseDB.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Licence introuvable")
    lic.device_uuid = None
    db.commit()
    return {"message": "Appareil dissocié avec succès"}

# ----------------- Routes Application Flutter -----------------
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
        # Multi-poste : si besoin de restreindre à un seul poste, décommentez la ligne ci-dessous :
        # raise HTTPException(status_code=403, detail="Licence déjà utilisée sur un autre appareil.")
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
