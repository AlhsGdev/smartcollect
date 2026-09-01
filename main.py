import os
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# CONFIGURATION GLOBALE
# ==========================================
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

class SelfRegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    organization: Optional[str] = ""
    device_id: str

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
# ROUTES PUBLIQUES
# ==========================================
@app.get("/")
def read_root():
    return {"status": "online", "service": "SmartCollect License API (FastAPI)"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# ==========================================
# ROUTE AUTO-ENREGISTREMENT SANS EMAIL (SELF-SERVICE)
# ==========================================
@app.post("/api/license/self-register")
def self_register_license(req: SelfRegisterRequest, db: Session = Depends(get_db)):
    clean_device_id = req.device_id.strip()

    # 1. Vérifier si l'appareil possède déjà une licence active
    licenses = db.query(LicenseKey).all()
    for lic in licenses:
        try:
            devs = json.loads(lic.device_uuid or "[]")
            if clean_device_id in devs:
                if not lic.is_active or lic.device_uuid == "REVOKED":
                    raise HTTPException(status_code=403, detail="Cet appareil a été suspendu ou révoqué.")
                return {
                    "status": "success",
                    "key": lic.key,
                    "first_name": lic.first_name,
                    "last_name": lic.last_name,
                    "organization": lic.organization,
                    "expires_at": lic.expires_at.strftime("%Y-%m-%d %H:%M:%S") if lic.expires_at else "Illimité",
                    "message": "Licence existante récupérée avec succès."
                }
        except Exception:
            continue

    # 2. Générer une nouvelle clé unique
    part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
    license_key = f"{part1}-{part2}-{part3}-{part4}"

    # 3. Attribuer une période d'essai automatique (ex: 30 jours pour 1 appareil)
    now = get_utc_now()
    trial_duration = timedelta(days=30)
    expires_at = now + trial_duration

    new_lic = LicenseKey(
        key=license_key,
        assigned_to_email=req.email.lower().strip(),
        first_name=req.first_name.strip(),
        last_name=req.last_name.strip(),
        organization=req.organization.strip() if req.organization else "",
        is_active=True,
        device_uuid=json.dumps([clean_device_id]),
        max_devices=1,
        duration_days=30,
        activated_at=now,
        expires_at=expires_at,
        created_at=now
    )
    db.add(new_lic)
    db.commit()
    db.refresh(new_lic)

    return {
        "status": "success",
        "key": license_key,
        "first_name": new_lic.first_name,
        "last_name": new_lic.last_name,
        "organization": new_lic.organization,
        "expires_at": new_lic.expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        "message": "Nouvelle licence d'essai activée avec succès !"
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
# ROUTE ACTIVATION MANUELLE FLUTTER
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

    return {
        "id": new_lic.id,
        "key": license_key,
        "email": new_lic.assigned_to_email,
        "duration": duration_str
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
