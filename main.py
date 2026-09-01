import os
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# CONFIGURATION BASE DE DONNÉES (NEON / RENDER)
# ==========================================
DEFAULT_DB_URL = "postgresql://neondb_owner:npg_NmxZaUb7n1Co@ep-odd-rice-axq1ordl-pooler.c-4.us-east-2.aws.neon.tech/neondb?sslmode=require"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Nettoyage d'éventuels paramètres incompatibles psycopg2
if "&channel_binding=require" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("&channel_binding=require", "")
if "?channel_binding=require" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("?channel_binding=require", "")

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 300
    engine_kwargs["pool_size"] = 5
    engine_kwargs["max_overflow"] = 10

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ==========================================
# MODÈLES SQLALCHEMY
# ==========================================
class LicenseKey(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(32), unique=True, index=True, nullable=False)
    phone_number = Column(String(50), nullable=False, index=True)
    first_name = Column(String(100), default="", nullable=True)
    last_name = Column(String(100), default="", nullable=True)
    organization = Column(String(150), default="", nullable=True)
    is_active = Column(Boolean, default=True)
    device_uuid = Column(Text, default="[]")
    max_devices = Column(Integer, default=1)
    duration_days = Column(Integer, default=30)
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

# ==========================================
# APPLICATION FASTAPI
# ==========================================
app = FastAPI(
    title="SmartCollect License Server",
    redirect_slashes=True,
)

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
class SelfRegisterPhoneRequest(BaseModel):
    first_name: str
    last_name: str
    phone_number: str
    organization: Optional[str] = ""

class FlutterVerifyRequest(BaseModel):
    key: str
    device_id: str
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    organization: Optional[str] = ""

# ==========================================
# ROUTES
# ==========================================
@app.get("/")
def home():
    return {
        "status": "online",
        "database": "Neon PostgreSQL",
        "message": "SmartCollect API is running"
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.post("/api/license/request-key")
@app.post("/api/license/request-key/")
def request_license_key(req: SelfRegisterPhoneRequest, db: Session = Depends(get_db)):
    clean_phone = req.phone_number.strip().replace(" ", "")

    existing_lic = db.query(LicenseKey).filter(LicenseKey.phone_number == clean_phone).first()
    if existing_lic:
        return {
            "status": "success",
            "message": "Une clé existe déjà pour ce numéro de téléphone !",
            "license_key": existing_lic.key
        }

    part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
    license_key = f"{part1}-{part2}-{part3}-{part4}"

    new_lic = LicenseKey(
        key=license_key,
        phone_number=clean_phone,
        first_name=req.first_name.strip(),
        last_name=req.last_name.strip(),
        organization=req.organization.strip() if req.organization else "",
        is_active=True,
        device_uuid="[]",
        max_devices=1,
        duration_days=30,
        created_at=get_utc_now()
    )
    db.add(new_lic)
    db.commit()
    db.refresh(new_lic)

    return {
        "status": "success",
        "message": "Clé générée avec succès !",
        "license_key": license_key
    }

@app.post("/api/license/verify")
@app.post("/api/license/verify/")
def verify_or_activate_flutter(req: FlutterVerifyRequest, db: Session = Depends(get_db)):
    clean_key = req.key.strip().upper()
    license_entry = db.query(LicenseKey).filter(LicenseKey.key == clean_key).first()

    if not license_entry:
        raise HTTPException(status_code=404, detail="Clé de licence introuvable.")

    if not license_entry.is_active or license_entry.device_uuid == "REVOKED":
        raise HTTPException(status_code=403, detail="Cette licence a été désactivée ou révoquée.")

    now = get_utc_now()
    if license_entry.expires_at and now > license_entry.expires_at:
        raise HTTPException(status_code=403, detail="Cette licence a expiré.")

    if not license_entry.activated_at:
        license_entry.activated_at = now
        license_entry.expires_at = now + timedelta(days=license_entry.duration_days or 30)

    try:
        devices: List[str] = json.loads(license_entry.device_uuid or "[]")
    except Exception:
        devices = []

    max_dev = license_entry.max_devices or 1
    if req.device_id not in devices:
        if len(devices) >= max_dev:
            raise HTTPException(status_code=403, detail="Limite d'appareils atteinte pour cette clé.")
        devices.append(req.device_id)
        license_entry.device_uuid = json.dumps(devices)

    db.commit()

    return {
        "status": "valid",
        "phone_number": license_entry.phone_number,
        "expires_at": license_entry.expires_at.strftime("%Y-%m-%d %H:%M:%S") if license_entry.expires_at else None
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
