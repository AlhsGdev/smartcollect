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
# CONFIGURATION BASE DE DONNÉES
# ==========================================
DEFAULT_DB_URL = "postgresql://neondb_owner:npg_NmxZaUb7n1Co@ep-odd-rice-axq1ordl-pooler.c-4.us-east-2.aws.neon.tech/neondb?sslmode=require"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

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
    phone_number = Column(String(50), default="", nullable=False, index=True)
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


class AppNews(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    content = Column(Text, default="", nullable=True)
    category = Column(String(50), default="news")
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
# APPLICATION FASTAPI
# ==========================================
app = FastAPI(title="SmartCollect API & Admin Server", redirect_slashes=True)

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

class AdminCreateLicenseRequest(BaseModel):
    phone_number: str
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
def home():
    return {
        "status": "online",
        "database": "Neon PostgreSQL",
        "service": "SmartCollect Unified API"
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

# ==========================================
# ROUTES FLUTTER
# ==========================================
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
        devices = json.loads(license_entry.device_uuid or "[]") if isinstance(license_entry.device_uuid, str) else []
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
        "phone_number": license_entry.phone_number or "",
        "expires_at": license_entry.expires_at.strftime("%Y-%m-%d %H:%M:%S") if license_entry.expires_at else None
    }

# ==========================================
# ROUTES ADMINISTRATION DES LICENCES (GUI)
# ==========================================
@app.get("/api/admin/licenses")
@app.get("/api/admin/licenses/")
def get_admin_licenses(db: Session = Depends(get_db)):
    try:
        licenses = db.query(LicenseKey).order_by(LicenseKey.id.desc()).all()
        results = []

        for item in licenses:
            try:
                raw_dev = str(item.device_uuid or "[]")
                dev_list = json.loads(raw_dev) if raw_dev not in ["REVOKED", ""] else []
            except Exception:
                dev_list = []

            first = str(item.first_name or "")
            last = str(item.last_name or "")
            full_name = f"{first} {last}".strip()

            results.append({
                "id": item.id,
                "key": str(item.key or ""),
                "phone_number": str(item.phone_number or "—"),
                "user_name": full_name if full_name else "Non activé",
                "organization": str(item.organization or "—"),
                "used_devices": len(dev_list),
                "max_devices": item.max_devices if item.max_devices is not None else 1,
                "is_active": bool(item.is_active and str(item.device_uuid) != "REVOKED"),
                "created_at": item.created_at.isoformat() if item.created_at else "",
                "expires_at": item.expires_at.isoformat() if item.expires_at else None
            })

        return results
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Erreur SQL/Python: {str(err)}")

@app.post("/api/admin/licenses/create")
@app.post("/api/admin/licenses/create/")
def create_admin_license(req: AdminCreateLicenseRequest, db: Session = Depends(get_db)):
    part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
    license_key = f"{part1}-{part2}-{part3}-{part4}"

    days = 30
    unit = req.duration_unit.lower()
    if "mois" in unit:
        days = req.duration_val * 30
    elif "an" in unit:
        days = req.duration_val * 365
    elif "jour" in unit:
        days = req.duration_val
    else:
        days = req.duration_val

    new_lic = LicenseKey(
        key=license_key,
        phone_number=req.phone_number.strip().replace(" ", ""),
        is_active=True,
        device_uuid="[]",
        max_devices=req.max_devices,
        duration_days=days,
        created_at=get_utc_now()
    )
    db.add(new_lic)
    db.commit()
    db.refresh(new_lic)

    return {
        "id": new_lic.id,
        "key": license_key,
        "phone_number": new_lic.phone_number
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

# ==========================================
# ROUTES ACTUALITÉS & ASTUCES
# ==========================================
@app.get("/api/news")
@app.get("/api/news/")
def get_all_news(db: Session = Depends(get_db)):
    try:
        news_items = db.query(AppNews).order_by(AppNews.id.desc()).all()
        results = []
        for n in news_items:
            results.append({
                "id": str(n.id),
                "title": str(n.title or ""),
                "summary": str(n.summary or ""),
                "content": str(n.content or ""),
                "category": str(n.category or "news"),
                "version": n.version,
                "download_url": n.download_url,
                "created_at": n.created_at.isoformat() if n.created_at else get_utc_now().isoformat()
            })
        return results
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Erreur SQL News: {str(err)}")

@app.post("/api/admin/news/create")
@app.post("/api/admin/news/create/")
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
