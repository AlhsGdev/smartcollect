# main.py
# ═══════════════════════════════════════════════════════════
#  SmartCollect — Backend API & Admin Server
#  ✅ FastAPI + SQLAlchemy + PostgreSQL
#  ✅ Comptes utilisateurs (email + password)
#  ✅ Multi-appareils par compte (auto-déconnexion)
#  ✅ Plans tarifaires (Basic / Pro / Business)
#  ✅ Upgrade via Play Store
#  ⚠ Système Licence WhatsApp SUPPRIMÉ
# ═══════════════════════════════════════════════════════════

import os
import json
import secrets
import time
import traceback
import re
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Text, Float, text,
    ForeignKey, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
    print("⚠️  [WARN] bcrypt non installé. Ajoute 'bcrypt' dans requirements.txt")
    print("⚠️  [WARN] Fallback SHA256 utilisé (moins sécurisé).")

# ═══════════════════════════════════════════════════════════
# CONFIGURATION GLOBALE
# ═══════════════════════════════════════════════════════════
DEFAULT_TRIAL_DAYS = 7
DEFAULT_TRIAL_UNIT = "Jours"
SESSION_DURATION_DAYS = 30

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()

if not ADMIN_API_KEY:
    print("⚠️  [WARN] ADMIN_API_KEY non définie.")
    print("⚠️  [WARN] Ajoute ADMIN_API_KEY=xxxx dans les variables d'environnement Render.")
    print("⚠️  [WARN] Les routes /api/admin/* seront inaccessibles (401).")

# ═══════════════════════════════════════════════════════════
# CONFIGURATION BASE DE DONNÉES
# ═══════════════════════════════════════════════════════════
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
    print("❌ [FATAL] DATABASE_URL non définie !")
    raise RuntimeError("DATABASE_URL requis.")

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
    engine_kwargs["connect_args"] = {
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }


def _create_engine_with_retry(url, kwargs, retries=3):
    for attempt in range(retries):
        try:
            eng = create_engine(url, **kwargs)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            print(f"[DB] Connexion réussie (tentative {attempt + 1})")
            return eng
        except Exception as e:
            print(f"[DB] Tentative {attempt + 1}/{retries} échouée: {e}")
            if attempt == retries - 1:
                raise
            time.sleep(2)


engine = _create_engine_with_retry(DATABASE_URL, engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ═══════════════════════════════════════════════════════════
# 🛡️  DÉPENDANCE AUTH ADMIN
# ═══════════════════════════════════════════════════════════
async def require_admin_key(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    if not ADMIN_API_KEY:
        raise HTTPException(503, "Service admin désactivé (ADMIN_API_KEY non configurée).")
    if not x_admin_key or x_admin_key.strip() != ADMIN_API_KEY:
        raise HTTPException(401, "Clé admin invalide ou manquante.")
    return True


# ═══════════════════════════════════════════════════════════
# 🛡️  RATE LIMITING
# ═══════════════════════════════════════════════════════════
_rate_limit_store: dict = {}
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_REQUESTS = 10


def check_rate_limit(identifier: str):
    now = time.time()
    history = _rate_limit_store.get(identifier, [])
    history = [t for t in history if now - t < RATE_LIMIT_WINDOW_SEC]
    if len(history) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(429, "Trop de requêtes. Réessayez dans une minute.")
    history.append(now)
    _rate_limit_store[identifier] = history


# ═══════════════════════════════════════════════════════════
# MODÈLES SQLALCHEMY
# ═══════════════════════════════════════════════════════════
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


class AppConfig(Base):
    __tablename__ = "app_config"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False, index=True)
    value = Column(String(255), default="")
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)


class LicensePlan(Base):
    __tablename__ = "license_plans"

    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(255), default="")
    price_monthly_usd = Column(Float, default=5.0)
    price_yearly_usd = Column(Float, default=40.0)
    max_tables = Column(Integer, default=3)
    max_rows_per_table = Column(Integer, default=500)
    allow_export = Column(Boolean, default=True)
    allow_cloud_backup = Column(Boolean, default=False)
    allow_bulk_export = Column(Boolean, default=False)
    allow_merge_tables = Column(Boolean, default=False)
    allow_custom_branding = Column(Boolean, default=False)
    allow_reminders = Column(Boolean, default=False)
    max_devices = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    google_sub = Column(String(255), nullable=True, index=True)

    first_name = Column(String(100), default="", nullable=True)
    last_name = Column(String(100), default="", nullable=True)
    phone_number = Column(String(50), default="", nullable=True)

    plan_code = Column(String(20), default="trial")
    is_yearly = Column(Boolean, default=False)
    is_trial = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime, nullable=True)

    purchase_token = Column(Text, nullable=True)
    purchase_platform = Column(String(20), default="play_store")

    created_at = Column(DateTime, default=get_utc_now)
    last_login_at = Column(DateTime, nullable=True)
    last_login_device = Column(String(255), nullable=True)


class AccountDevice(Base):
    __tablename__ = "account_devices"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)

    device_id = Column(String(255), nullable=False, index=True)
    device_name = Column(String(150), default="")
    platform = Column(String(20), default="unknown")
    app_version = Column(String(30), default="")

    session_token = Column(String(128), unique=True, index=True, nullable=False)
    session_expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True, index=True)

    created_at = Column(DateTime, default=get_utc_now)
    last_seen_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(100), default="")


Index("ix_account_devices_account_active", AccountDevice.account_id, AccountDevice.is_active)
Index("ix_account_devices_device_active", AccountDevice.device_id, AccountDevice.is_active)


# ═══════════════════════════════════════════════════════════
# MIGRATIONS
# ═══════════════════════════════════════════════════════════
print("[startup] Début des migrations...")

migrations = [
    ("CREATE TABLE accounts IF NOT EXISTS",
     "CREATE TABLE IF NOT EXISTS accounts ("
     "  id SERIAL PRIMARY KEY,"
     "  email VARCHAR(255) UNIQUE NOT NULL,"
     "  password_hash VARCHAR(255) NOT NULL,"
     "  google_sub VARCHAR(255),"
     "  first_name VARCHAR(100) DEFAULT '',"
     "  last_name VARCHAR(100) DEFAULT '',"
     "  phone_number VARCHAR(50) DEFAULT '',"
     "  plan_code VARCHAR(20) DEFAULT 'trial',"
     "  is_yearly BOOLEAN DEFAULT FALSE,"
     "  is_trial BOOLEAN DEFAULT TRUE,"
     "  is_active BOOLEAN DEFAULT TRUE,"
     "  expires_at TIMESTAMP,"
     "  purchase_token TEXT,"
     "  purchase_platform VARCHAR(20) DEFAULT 'play_store',"
     "  created_at TIMESTAMP DEFAULT NOW(),"
     "  last_login_at TIMESTAMP,"
     "  last_login_device VARCHAR(255)"
     ")"),
    ("CREATE INDEX accounts.email",
     "CREATE INDEX IF NOT EXISTS ix_accounts_email ON accounts(email)"),
    ("CREATE TABLE account_devices IF NOT EXISTS",
     "CREATE TABLE IF NOT EXISTS account_devices ("
     "  id SERIAL PRIMARY KEY,"
     "  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,"
     "  device_id VARCHAR(255) NOT NULL,"
     "  device_name VARCHAR(150) DEFAULT '',"
     "  platform VARCHAR(20) DEFAULT 'unknown',"
     "  app_version VARCHAR(30) DEFAULT '',"
     "  session_token VARCHAR(128) UNIQUE NOT NULL,"
     "  session_expires_at TIMESTAMP NOT NULL,"
     "  is_active BOOLEAN DEFAULT TRUE,"
     "  created_at TIMESTAMP DEFAULT NOW(),"
     "  last_seen_at TIMESTAMP DEFAULT NOW(),"
     "  revoked_at TIMESTAMP,"
     "  revoked_reason VARCHAR(100) DEFAULT ''"
     ")"),
    ("CREATE INDEX account_devices.account_id",
     "CREATE INDEX IF NOT EXISTS ix_account_devices_account ON account_devices(account_id)"),
    ("CREATE INDEX account_devices.device_id",
     "CREATE INDEX IF NOT EXISTS ix_account_devices_device ON account_devices(device_id)"),
    ("CREATE INDEX account_devices.session_token",
     "CREATE INDEX IF NOT EXISTS ix_account_devices_session ON account_devices(session_token)"),
    ("UPDATE plans: Pro max_devices = 3",
     "UPDATE license_plans SET max_devices = 3 WHERE code = 'pro'"),
    ("UPDATE plans: Basic max_tables = 10",
     "UPDATE license_plans SET max_tables = 10 WHERE code = 'basic'"),
]

for name, sql in migrations:
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
        print(f"[migration OK] {name}")
    except Exception as e:
        print(f"[migration SKIP] {name} : {e}")

try:
    Base.metadata.create_all(bind=engine)
    print("[startup] Tables créées/vérifiées.")
except Exception as e:
    print(f"[startup] Erreur create_all : {e}")


# ═══════════════════════════════════════════════════════════
# SEED DES PLANS PAR DÉFAUT
# ═══════════════════════════════════════════════════════════
def _seed_default_plans():
    db = SessionLocal()
    try:
        defaults = [
            {
                "code": "trial",
                "name": "Essai",
                "description": "Essai gratuit 7 jours - 1 tableau, 50 lignes",
                "price_monthly_usd": 0.0,
                "price_yearly_usd": 0.0,
                "max_tables": 1,
                "max_rows_per_table": 50,
                "allow_export": False,
                "allow_cloud_backup": False,
                "allow_bulk_export": False,
                "allow_merge_tables": False,
                "allow_custom_branding": False,
                "allow_reminders": False,
                "max_devices": 1,
                "display_order": 0,
            },
            {
                "code": "basic",
                "name": "Basic",
                "description": "Pour les indépendants - 10 tableaux, 500 lignes, exports",
                "price_monthly_usd": 5.0,
                "price_yearly_usd": 40.0,
                "max_tables": 10,
                "max_rows_per_table": 500,
                "allow_export": True,
                "allow_cloud_backup": False,
                "allow_bulk_export": False,
                "allow_merge_tables": False,
                "allow_custom_branding": False,
                "allow_reminders": False,
                "max_devices": 1,
                "display_order": 1,
            },
            {
                "code": "pro",
                "name": "Pro",
                "description": "Pour les PME - Illimité + publipostage + cloud",
                "price_monthly_usd": 10.0,
                "price_yearly_usd": 80.0,
                "max_tables": 999999,
                "max_rows_per_table": 999999,
                "allow_export": True,
                "allow_cloud_backup": True,
                "allow_bulk_export": True,
                "allow_merge_tables": True,
                "allow_custom_branding": True,
                "allow_reminders": True,
                "max_devices": 3,
                "display_order": 2,
            },
            {
                "code": "business",
                "name": "Business",
                "description": "Pour les organisations - Multi-appareils + support",
                "price_monthly_usd": 25.0,
                "price_yearly_usd": 200.0,
                "max_tables": 999999,
                "max_rows_per_table": 999999,
                "allow_export": True,
                "allow_cloud_backup": True,
                "allow_bulk_export": True,
                "allow_merge_tables": True,
                "allow_custom_branding": True,
                "allow_reminders": True,
                "max_devices": 5,
                "display_order": 3,
            },
        ]
        for p in defaults:
            existing = db.query(LicensePlan).filter(LicensePlan.code == p["code"]).first()
            if not existing:
                db.add(LicensePlan(**p))
        db.commit()
        print("[seed] Plans par défaut OK")
    except Exception as e:
        print(f"[seed] Erreur : {e}")
        db.rollback()
    finally:
        db.close()


_seed_default_plans()

print("[startup] Migrations terminées.")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# HELPERS DURÉE
# ═══════════════════════════════════════════════════════════
def _duration_to_days_float(val: int, unit: str) -> float:
    u = (unit or "").lower().strip()
    if "minute" in u or u == "min" or u == "mn":
        return val / (24 * 60)
    if "heure" in u or u == "h" or u == "hr" or u == "hrs":
        return val / 24
    if "jour" in u or u == "day" or u == "days" or u == "j":
        return float(val)
    if "mois" in u or u == "month" or u == "months":
        return float(val * 30)
    if "an" in u or u == "year" or u == "years":
        return float(val * 365)
    return float(val)


def _duration_to_timedelta(val: int, unit: str) -> timedelta:
    u = (unit or "").lower().strip()
    if "minute" in u or u == "min" or u == "mn":
        return timedelta(minutes=val)
    if "heure" in u or u == "h" or u == "hr" or u == "hrs":
        return timedelta(hours=val)
    if "jour" in u or u == "day" or u == "days" or u == "j":
        return timedelta(days=val)
    if "mois" in u or u == "month" or u == "months":
        return timedelta(days=val * 30)
    if "an" in u or u == "year" or u == "years":
        return timedelta(days=val * 365)
    return timedelta(days=val)


# ═══════════════════════════════════════════════════════════
# HELPERS AUTH
# ═══════════════════════════════════════════════════════════
def _hash_password(password: str) -> str:
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    salt = "smartcollect_fallback_salt_v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _verify_password(password: str, password_hash: str) -> bool:
    if BCRYPT_AVAILABLE:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except Exception:
            return False
    salt = "smartcollect_fallback_salt_v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == password_hash


def _generate_session_token() -> str:
    return secrets.token_urlsafe(64)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _validate_email(email: str) -> bool:
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def _get_plan_max_devices(plan_code: str, db: Session) -> int:
    plan = db.query(LicensePlan).filter(LicensePlan.code == plan_code).first()
    if plan:
        return max(1, plan.max_devices or 1)
    fallbacks = {"trial": 1, "basic": 1, "pro": 3, "business": 5}
    return fallbacks.get(plan_code, 1)


def _get_plan_limits(plan_code: str, db: Session) -> dict:
    plan = db.query(LicensePlan).filter(LicensePlan.code == plan_code).first()
    if plan:
        return {
            "max_tables": plan.max_tables,
            "max_rows_per_table": plan.max_rows_per_table,
            "allow_export": bool(plan.allow_export),
            "allow_cloud_backup": bool(plan.allow_cloud_backup),
            "allow_bulk_export": bool(plan.allow_bulk_export),
            "allow_merge_tables": bool(plan.allow_merge_tables),
            "allow_custom_branding": bool(plan.allow_custom_branding),
            "allow_reminders": bool(plan.allow_reminders),
            "max_devices": plan.max_devices,
        }
    return {
        "max_tables": 1 if plan_code == "trial" else 999999,
        "max_rows_per_table": 50 if plan_code == "trial" else 999999,
        "allow_export": plan_code != "trial",
        "allow_cloud_backup": plan_code in ("pro", "business"),
        "allow_bulk_export": plan_code in ("pro", "business"),
        "allow_merge_tables": plan_code in ("pro", "business"),
        "allow_custom_branding": plan_code in ("pro", "business"),
        "allow_reminders": plan_code in ("pro", "business"),
        "max_devices": {"trial": 1, "basic": 1, "pro": 3, "business": 5}.get(plan_code, 1),
    }


def _enforce_device_limit(account: Account, device_id: str, device_name: str,
                           platform: str, app_version: str,
                           db: Session) -> AccountDevice:
    """
    Vérifie/ajoute un device à un compte.
    Si la limite est atteinte → désactive le plus ancien.
    """
    clean_device = (device_id or "").strip().upper()
    max_devices = _get_plan_max_devices(account.plan_code, db)

    existing = db.query(AccountDevice).filter(
        AccountDevice.account_id == account.id,
        AccountDevice.device_id == clean_device,
    ).first()

    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.revoked_at = None
            existing.revoked_reason = ""
        existing.session_token = _generate_session_token()
        existing.session_expires_at = get_utc_now() + timedelta(days=SESSION_DURATION_DAYS)
        existing.last_seen_at = get_utc_now()
        existing.device_name = device_name or existing.device_name
        existing.platform = platform or existing.platform
        existing.app_version = app_version or existing.app_version
        return existing

    active_devices = db.query(AccountDevice).filter(
        AccountDevice.account_id == account.id,
        AccountDevice.is_active == True,
    ).order_by(AccountDevice.last_seen_at.desc()).all()

    while len(active_devices) >= max_devices:
        oldest = active_devices.pop()
        oldest.is_active = False
        oldest.revoked_at = get_utc_now()
        oldest.revoked_reason = f"Auto-déconnecté (limite {max_devices} appareils atteinte)"
        print(f"[DEVICE LIMIT] Compte {account.email} : révoqué {oldest.device_id[:16]}...")

    new_device = AccountDevice(
        account_id=account.id,
        device_id=clean_device,
        device_name=device_name or "Appareil sans nom",
        platform=platform or "unknown",
        app_version=app_version or "",
        session_token=_generate_session_token(),
        session_expires_at=get_utc_now() + timedelta(days=SESSION_DURATION_DAYS),
        is_active=True,
    )
    db.add(new_device)
    return new_device


def _account_to_access_payload(account: Account, db: Session) -> dict:
    """Construit le payload d'accès pour un compte."""
    plan_code = account.plan_code or "trial"
    limits = _get_plan_limits(plan_code, db)

    plan = db.query(LicensePlan).filter(LicensePlan.code == plan_code).first()
    plan_name = plan.name if plan else plan_code.capitalize()

    return {
        "mode": "trial" if account.is_trial else "full",
        "is_trial": bool(account.is_trial),
        "plan_code": plan_code,
        "plan_name": plan_name,
        "expires_at": account.expires_at.isoformat() if account.expires_at else None,
        "max_tables": limits["max_tables"],
        "max_rows_per_table": limits["max_rows_per_table"],
        "allow_export": limits["allow_export"],
        "allow_cloud_backup": limits["allow_cloud_backup"],
        "allow_bulk_export": limits["allow_bulk_export"],
        "allow_merge_tables": limits["allow_merge_tables"],
        "allow_custom_branding": limits["allow_custom_branding"],
        "allow_reminders": limits["allow_reminders"],
        "max_devices": limits["max_devices"],
    }


# ═══════════════════════════════════════════════════════════
# HELPERS CONFIG DYNAMIQUE
# ═══════════════════════════════════════════════════════════
def _get_default_trial_config(db: Session) -> tuple:
    defaults = {
        "default_trial_val": "7",
        "default_trial_unit": "Jours",
    }
    results = {}
    for k in defaults:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        results[k] = row.value if row else defaults[k]

    try:
        val = int(results["default_trial_val"])
    except ValueError:
        val = 7
    unit = results["default_trial_unit"] or "Jours"
    days = _duration_to_days_float(val, unit)

    return val, unit, days


def _set_default_trial_config(db: Session, val: int, unit: str):
    for k, v in [
        ("default_trial_val", str(val)),
        ("default_trial_unit", unit),
    ]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(AppConfig(key=k, value=v))
    db.commit()


# ═══════════════════════════════════════════════════════════
# APPLICATION FASTAPI
# ═══════════════════════════════════════════════════════════
app = FastAPI(title="SmartCollect API & Admin Server", redirect_slashes=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════
# SCHÉMAS PYDANTIC
# ═══════════════════════════════════════════════════════════
class UpdatePlanRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    price_monthly_usd: Optional[float] = Field(None, ge=0)
    price_yearly_usd: Optional[float] = Field(None, ge=0)
    max_tables: Optional[int] = Field(None, ge=1)
    max_rows_per_table: Optional[int] = Field(None, ge=1)
    allow_export: Optional[bool] = None
    allow_cloud_backup: Optional[bool] = None
    allow_bulk_export: Optional[bool] = None
    allow_merge_tables: Optional[bool] = None
    allow_custom_branding: Optional[bool] = None
    allow_reminders: Optional[bool] = None
    max_devices: Optional[int] = Field(None, ge=1, le=50)
    is_active: Optional[bool] = None
    display_order: Optional[int] = Field(None, ge=0)


class NewsCreateRequest(BaseModel):
    title: str = Field(..., max_length=255)
    summary: str = Field(..., max_length=2000)
    content: Optional[str] = ""
    category: str = Field("news", max_length=50)
    version: Optional[str] = None
    download_url: Optional[str] = None


class DefaultTrialConfigRequest(BaseModel):
    duration_val: int = Field(..., ge=1, le=9999)
    duration_unit: str = Field(..., max_length=20)


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=100)
    first_name: str = Field("", max_length=100)
    last_name: str = Field("", max_length=100)
    phone_number: str = Field("", max_length=50)
    device_id: str = Field(..., min_length=8, max_length=255)
    device_name: str = Field("", max_length=150)
    platform: str = Field("unknown", max_length=20)
    app_version: str = Field("", max_length=30)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=1, max_length=100)
    device_id: str = Field(..., min_length=8, max_length=255)
    device_name: str = Field("", max_length=150)
    platform: str = Field("unknown", max_length=20)
    app_version: str = Field("", max_length=30)


class VerifySessionRequest(BaseModel):
    session_token: str = Field(..., min_length=16, max_length=128)
    device_id: str = Field(..., min_length=8, max_length=255)


class LogoutRequest(BaseModel):
    session_token: str = Field(..., min_length=16, max_length=128)


class AccountUpgradeRequest(BaseModel):
    session_token: str = Field(..., min_length=16, max_length=128)
    plan_code: str = Field(..., pattern="^(basic|pro|business)$")
    is_yearly: bool = False
    purchase_token: Optional[str] = None
    purchase_platform: str = Field("play_store", max_length=20)


class RevokeDeviceRequest(BaseModel):
    session_token: str = Field(..., min_length=16, max_length=128)
    device_id_to_revoke: str = Field(..., min_length=8, max_length=255)


# ✅ NOUVEAUX SCHÉMAS ADMIN — COMPTES
class AdminUpdateAccountRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    plan_code: Optional[str] = Field(None, pattern="^(trial|basic|pro|business)$")
    is_yearly: Optional[bool] = None
    is_trial: Optional[bool] = None
    is_active: Optional[bool] = None


class AdminGrantFullAccessRequest(BaseModel):
    plan_code: str = Field(..., pattern="^(basic|pro|business)$")
    is_yearly: bool = False
    duration_val: int = Field(..., ge=1, le=9999)
    duration_unit: str = Field(..., max_length=20)
    duration_mode: str = Field("replace", pattern="^(add|replace)$")
    reset_expiry: bool = True


class AdminResetToTrialRequest(BaseModel):
    duration_val: int = Field(..., ge=1, le=9999)
    duration_unit: str = Field(..., max_length=20)
    duration_mode: str = Field("replace", pattern="^(add|replace)$")


# ═══════════════════════════════════════════════════════════
# ROUTES AUTH (email + password)
# ═══════════════════════════════════════════════════════════
@app.post("/api/auth/register")
@app.post("/api/auth/register/")
def auth_register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Créer un nouveau compte utilisateur."""
    try:
        email = _normalize_email(req.email)
        if not _validate_email(email):
            raise HTTPException(400, "Email invalide.")
        if len(req.password) < 8:
            raise HTTPException(400, "Le mot de passe doit faire au moins 8 caractères.")

        existing = db.query(Account).filter(Account.email == email).first()
        if existing:
            raise HTTPException(409, "Cet email est déjà utilisé.")

        def_val, def_unit, def_days_float = _get_default_trial_config(db)

        now = get_utc_now()
        new_account = Account(
            email=email,
            password_hash=_hash_password(req.password),
            first_name=(req.first_name or "").strip(),
            last_name=(req.last_name or "").strip(),
            phone_number=(req.phone_number or "").strip(),
            plan_code="trial",
            is_trial=True,
            is_active=True,
            expires_at=now + _duration_to_timedelta(def_val, def_unit),
            created_at=now,
            last_login_at=now,
            last_login_device=req.device_id.strip().upper(),
        )
        db.add(new_account)
        db.flush()

        device = _enforce_device_limit(
            account=new_account,
            device_id=req.device_id,
            device_name=req.device_name,
            platform=req.platform,
            app_version=req.app_version,
            db=db,
        )

        db.commit()
        db.refresh(new_account)
        db.refresh(device)

        access = _account_to_access_payload(new_account, db)
        return {
            "status": "success",
            "message": "Compte créé avec succès.",
            "account": {
                "id": new_account.id,
                "email": new_account.email,
                "first_name": new_account.first_name,
                "last_name": new_account.last_name,
                "plan_code": new_account.plan_code,
                "is_trial": new_account.is_trial,
                "expires_at": new_account.expires_at.isoformat() if new_account.expires_at else None,
            },
            "session_token": device.session_token,
            "session_expires_at": device.session_expires_at.isoformat(),
            "access": access,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[AUTH-REGISTER ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur serveur: {str(e)}")


@app.post("/api/auth/login")
@app.post("/api/auth/login/")
def auth_login(req: LoginRequest, db: Session = Depends(get_db)):
    """Connexion + gestion multi-appareils."""
    try:
        email = _normalize_email(req.email)
        clean_device = req.device_id.strip().upper()

        check_rate_limit(f"login:{email}")

        account = db.query(Account).filter(Account.email == email).first()
        if not account:
            raise HTTPException(401, "Email ou mot de passe incorrect.")

        if not account.is_active:
            raise HTTPException(403, "Ce compte est désactivé. Contactez l'administrateur.")

        if not _verify_password(req.password, account.password_hash):
            raise HTTPException(401, "Email ou mot de passe incorrect.")

        now = get_utc_now()
        if account.expires_at and now > account.expires_at:
            raise HTTPException(
                403,
                "Votre abonnement a expiré. Renouvelez-le pour continuer."
            )

        device = _enforce_device_limit(
            account=account,
            device_id=clean_device,
            device_name=req.device_name,
            platform=req.platform,
            app_version=req.app_version,
            db=db,
        )

        account.last_login_at = now
        account.last_login_device = clean_device

        db.commit()
        db.refresh(account)
        db.refresh(device)

        access = _account_to_access_payload(account, db)
        return {
            "status": "success",
            "message": "Connexion réussie.",
            "account": {
                "id": account.id,
                "email": account.email,
                "first_name": account.first_name,
                "last_name": account.last_name,
                "plan_code": account.plan_code,
                "is_trial": account.is_trial,
                "expires_at": account.expires_at.isoformat() if account.expires_at else None,
            },
            "session_token": device.session_token,
            "session_expires_at": device.session_expires_at.isoformat(),
            "access": access,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[AUTH-LOGIN ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur serveur: {str(e)}")


@app.post("/api/auth/verify-session")
@app.post("/api/auth/verify-session/")
def auth_verify_session(req: VerifySessionRequest, db: Session = Depends(get_db)):
    """Vérifier une session au démarrage de l'app."""
    try:
        clean_device = req.device_id.strip().upper()
        now = get_utc_now()

        device = db.query(AccountDevice).filter(
            AccountDevice.session_token == req.session_token,
            AccountDevice.device_id == clean_device,
        ).first()

        if not device:
            raise HTTPException(401, "Session introuvable.")

        if not device.is_active:
            raise HTTPException(403, "Cette session a été révoquée (déconnectée à distance).")

        if device.session_expires_at and now > device.session_expires_at:
            raise HTTPException(401, "Session expirée. Reconnectez-vous.")

        account = db.query(Account).filter(Account.id == device.account_id).first()
        if not account or not account.is_active:
            raise HTTPException(403, "Compte désactivé.")

        if account.expires_at and now > account.expires_at:
            raise HTTPException(403, "Abonnement expiré.")

        device.last_seen_at = now
        db.commit()
        db.refresh(account)

        access = _account_to_access_payload(account, db)
        return {
            "status": "valid",
            "account": {
                "id": account.id,
                "email": account.email,
                "first_name": account.first_name,
                "last_name": account.last_name,
                "plan_code": account.plan_code,
                "is_trial": account.is_trial,
                "expires_at": account.expires_at.isoformat() if account.expires_at else None,
            },
            "access": access,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[VERIFY-SESSION ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur serveur: {str(e)}")


@app.post("/api/auth/logout")
@app.post("/api/auth/logout/")
def auth_logout(req: LogoutRequest, db: Session = Depends(get_db)):
    """Déconnecte l'appareil courant."""
    try:
        device = db.query(AccountDevice).filter(
            AccountDevice.session_token == req.session_token
        ).first()

        if device:
            device.is_active = False
            device.revoked_at = get_utc_now()
            device.revoked_reason = "Déconnexion manuelle"
            db.commit()

        return {"status": "success", "message": "Déconnexion réussie."}
    except Exception as e:
        db.rollback()
        print(f"[LOGOUT ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur serveur: {str(e)}")


@app.get("/api/account/devices")
@app.get("/api/account/devices/")
def get_account_devices(session_token: str, db: Session = Depends(get_db)):
    """Liste tous les appareils actifs du compte."""
    try:
        device = db.query(AccountDevice).filter(
            AccountDevice.session_token == session_token
        ).first()

        if not device or not device.is_active:
            raise HTTPException(401, "Session invalide.")

        account = db.query(Account).filter(Account.id == device.account_id).first()
        if not account:
            raise HTTPException(404, "Compte introuvable.")

        all_devices = db.query(AccountDevice).filter(
            AccountDevice.account_id == account.id,
            AccountDevice.is_active == True,
        ).order_by(AccountDevice.last_seen_at.desc()).all()

        max_devices = _get_plan_max_devices(account.plan_code, db)

        return {
            "status": "success",
            "plan_code": account.plan_code,
            "max_devices": max_devices,
            "used_devices": len(all_devices),
            "current_device_id": device.device_id,
            "devices": [
                {
                    "device_id": d.device_id,
                    "device_name": d.device_name or "Appareil sans nom",
                    "platform": d.platform,
                    "app_version": d.app_version,
                    "is_current": d.device_id == device.device_id,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                }
                for d in all_devices
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ACCOUNT-DEVICES ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur serveur: {str(e)}")


@app.post("/api/account/devices/revoke")
@app.post("/api/account/devices/revoke/")
def revoke_account_device(req: RevokeDeviceRequest, db: Session = Depends(get_db)):
    """Révoque un autre appareil à distance."""
    try:
        clean_target = req.device_id_to_revoke.strip().upper()

        current_device = db.query(AccountDevice).filter(
            AccountDevice.session_token == req.session_token
        ).first()

        if not current_device or not current_device.is_active:
            raise HTTPException(401, "Session invalide.")

        account = db.query(Account).filter(Account.id == current_device.account_id).first()
        if not account:
            raise HTTPException(404, "Compte introuvable.")

        if current_device.device_id.strip().upper() == clean_target:
            raise HTTPException(400, "Vous ne pouvez pas révoquer l'appareil courant.")

        target = db.query(AccountDevice).filter(
            AccountDevice.account_id == account.id,
            AccountDevice.device_id == clean_target,
            AccountDevice.is_active == True,
        ).first()

        if not target:
            raise HTTPException(404, "Appareil introuvable ou déjà révoqué.")

        target.is_active = False
        target.revoked_at = get_utc_now()
        target.revoked_reason = "Révoqué depuis un autre appareil"
        db.commit()

        return {
            "status": "success",
            "message": f"Appareil '{target.device_name or target.device_id[:12]}' révoqué.",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[REVOKE-DEVICE ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur serveur: {str(e)}")


@app.post("/api/account/upgrade")
@app.post("/api/account/upgrade/")
def account_upgrade(req: AccountUpgradeRequest, db: Session = Depends(get_db)):
    """Met à jour le plan du compte après un achat Play Store."""
    try:
        device = db.query(AccountDevice).filter(
            AccountDevice.session_token == req.session_token
        ).first()

        if not device or not device.is_active:
            raise HTTPException(401, "Session invalide.")

        account = db.query(Account).filter(Account.id == device.account_id).first()
        if not account:
            raise HTTPException(404, "Compte introuvable.")

        plan = db.query(LicensePlan).filter(LicensePlan.code == req.plan_code).first()
        if not plan:
            raise HTTPException(404, f"Plan '{req.plan_code}' introuvable.")

        now = get_utc_now()
        duration_days = 365 if req.is_yearly else 30

        account.plan_code = req.plan_code
        account.is_yearly = req.is_yearly
        account.is_trial = False
        account.purchase_token = req.purchase_token
        account.purchase_platform = req.purchase_platform
        account.expires_at = now + timedelta(days=duration_days)
        db.commit()
        db.refresh(account)

        new_max = _get_plan_max_devices(account.plan_code, db)
        active_count = db.query(AccountDevice).filter(
            AccountDevice.account_id == account.id,
            AccountDevice.is_active == True,
        ).count()

        access = _account_to_access_payload(account, db)
        return {
            "status": "success",
            "message": f"Plan {plan.name} activé.",
            "plan_code": account.plan_code,
            "is_yearly": account.is_yearly,
            "expires_at": account.expires_at.isoformat() if account.expires_at else None,
            "max_devices": new_max,
            "active_devices": active_count,
            "access": access,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[ACCOUNT-UPGRADE ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur serveur: {str(e)}")


# ═══════════════════════════════════════════════════════════
# ROUTES PUBLIQUES
# ═══════════════════════════════════════════════════════════
@app.get("/")
def home():
    return {
        "status": "online",
        "database": "PostgreSQL",
        "service": "SmartCollect Unified API",
        "default_trial_days": DEFAULT_TRIAL_DAYS,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/api/plans")
@app.get("/api/plans/")
def get_public_plans(db: Session = Depends(get_db)):
    """Retourne les plans disponibles avec tarifs et limites."""
    plans = (
        db.query(LicensePlan)
        .filter(LicensePlan.is_active == True)
        .order_by(LicensePlan.display_order.asc())
        .all()
    )

    result = []
    for p in plans:
        full_year = float(p.price_monthly_usd or 0) * 12
        discount = 0.0
        if full_year > 0 and p.price_yearly_usd:
            discount = round((1 - (p.price_yearly_usd / full_year)) * 100, 1)

        result.append({
            "code": p.code,
            "name": p.name,
            "description": p.description or "",
            "price_monthly_usd": float(p.price_monthly_usd or 0),
            "price_yearly_usd": float(p.price_yearly_usd or 0),
            "discount_yearly_pct": max(0.0, discount),
            "max_tables": p.max_tables,
            "max_rows_per_table": p.max_rows_per_table,
            "allow_export": bool(p.allow_export),
            "allow_cloud_backup": bool(p.allow_cloud_backup),
            "allow_bulk_export": bool(p.allow_bulk_export),
            "allow_merge_tables": bool(p.allow_merge_tables),
            "allow_custom_branding": bool(p.allow_custom_branding),
            "allow_reminders": bool(p.allow_reminders),
            "max_devices": p.max_devices,
        })
    return result


@app.get("/api/config/default-trial")
@app.get("/api/config/default-trial/")
def get_public_default_trial(db: Session = Depends(get_db)):
    val, unit, days = _get_default_trial_config(db)
    return {
        "duration_val": val,
        "duration_unit": unit,
        "duration_days": days,
    }


# ═══════════════════════════════════════════════════════════
# ROUTES ADMIN — PLANS
# ═══════════════════════════════════════════════════════════
@app.get("/api/admin/plans", dependencies=[Depends(require_admin_key)])
@app.get("/api/admin/plans/", dependencies=[Depends(require_admin_key)])
def admin_get_plans(db: Session = Depends(get_db)):
    plans = db.query(LicensePlan).order_by(LicensePlan.display_order.asc()).all()
    return [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "description": p.description or "",
            "price_monthly_usd": float(p.price_monthly_usd or 0),
            "price_yearly_usd": float(p.price_yearly_usd or 0),
            "max_tables": p.max_tables,
            "max_rows_per_table": p.max_rows_per_table,
            "allow_export": bool(p.allow_export),
            "allow_cloud_backup": bool(p.allow_cloud_backup),
            "allow_bulk_export": bool(p.allow_bulk_export),
            "allow_merge_tables": bool(p.allow_merge_tables),
            "allow_custom_branding": bool(p.allow_custom_branding),
            "allow_reminders": bool(p.allow_reminders),
            "max_devices": p.max_devices,
            "is_active": bool(p.is_active),
            "display_order": p.display_order,
        }
        for p in plans
    ]


@app.put("/api/admin/plans/{code}", dependencies=[Depends(require_admin_key)])
@app.put("/api/admin/plans/{code}/", dependencies=[Depends(require_admin_key)])
def admin_update_plan(code: str, req: UpdatePlanRequest, db: Session = Depends(get_db)):
    plan = db.query(LicensePlan).filter(LicensePlan.code == code).first()
    if not plan:
        raise HTTPException(404, "Plan introuvable.")

    for field, value in req.dict(exclude_unset=True).items():
        if value is not None:
            setattr(plan, field, value)

    db.commit()
    db.refresh(plan)
    return {
        "status": "success",
        "plan": {
            "code": plan.code,
            "name": plan.name,
            "price_monthly_usd": float(plan.price_monthly_usd or 0),
            "price_yearly_usd": float(plan.price_yearly_usd or 0),
            "max_tables": plan.max_tables,
            "max_rows_per_table": plan.max_rows_per_table,
            "max_devices": plan.max_devices,
        }
    }


# ═══════════════════════════════════════════════════════════
# ROUTES ADMIN — COMPTES UTILISATEURS
# ═══════════════════════════════════════════════════════════
@app.get("/api/admin/accounts", dependencies=[Depends(require_admin_key)])
@app.get("/api/admin/accounts/", dependencies=[Depends(require_admin_key)])
def admin_get_accounts(db: Session = Depends(get_db)):
    """Liste tous les comptes utilisateurs."""
    try:
        accounts = db.query(Account).order_by(Account.id.desc()).all()
        results = []
        for acc in accounts:
            device_count = db.query(AccountDevice).filter(
                AccountDevice.account_id == acc.id,
                AccountDevice.is_active == True,
            ).count()

            results.append({
                "id": acc.id,
                "email": acc.email,
                "first_name": acc.first_name or "",
                "last_name": acc.last_name or "",
                "phone_number": acc.phone_number or "",
                "plan_code": acc.plan_code,
                "is_yearly": bool(acc.is_yearly),
                "is_trial": bool(acc.is_trial),
                "is_active": bool(acc.is_active),
                "expires_at": acc.expires_at.isoformat() if acc.expires_at else None,
                "purchase_token": acc.purchase_token,
                "active_devices": device_count,
                "created_at": acc.created_at.isoformat() if acc.created_at else "",
                "last_login_at": acc.last_login_at.isoformat() if acc.last_login_at else None,
            })
        return results
    except Exception as err:
        print(f"[ADMIN-ACCOUNTS ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur SQL: {str(err)}")


@app.get("/api/admin/accounts/{account_id}/devices", dependencies=[Depends(require_admin_key)])
def admin_get_account_devices(account_id: int, db: Session = Depends(get_db)):
    """Liste les appareils d'un compte."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    devices = db.query(AccountDevice).filter(
        AccountDevice.account_id == account_id
    ).order_by(AccountDevice.last_seen_at.desc()).all()

    return {
        "status": "success",
        "account_email": account.email,
        "plan_code": account.plan_code,
        "devices": [
            {
                "id": d.id,
                "device_id": d.device_id,
                "device_name": d.device_name or "",
                "platform": d.platform,
                "app_version": d.app_version,
                "is_active": bool(d.is_active),
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None,
                "revoked_reason": d.revoked_reason or "",
            }
            for d in devices
        ],
    }


@app.post("/api/admin/accounts/{account_id}/revoke-device", dependencies=[Depends(require_admin_key)])
def admin_revoke_account_device(account_id: int, device_id: str, db: Session = Depends(get_db)):
    """Révoque un appareil d'un compte (support)."""
    device = db.query(AccountDevice).filter(
        AccountDevice.account_id == account_id,
        AccountDevice.device_id == device_id.strip().upper(),
    ).first()

    if not device:
        raise HTTPException(404, "Appareil introuvable.")

    device.is_active = False
    device.revoked_at = get_utc_now()
    device.revoked_reason = "Révoqué par l'administrateur"
    db.commit()

    return {
        "status": "success",
        "message": f"Appareil '{device.device_name or device.device_id[:12]}' révoqué.",
    }


@app.post("/api/admin/accounts/{account_id}/toggle-status", dependencies=[Depends(require_admin_key)])
def admin_toggle_account_status(account_id: int, db: Session = Depends(get_db)):
    """Active/désactive un compte."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    account.is_active = not account.is_active
    db.commit()

    return {
        "status": "success",
        "is_active": account.is_active,
    }


@app.put("/api/admin/accounts/{account_id}", dependencies=[Depends(require_admin_key)])
@app.put("/api/admin/accounts/{account_id}/", dependencies=[Depends(require_admin_key)])
def admin_update_account(
    account_id: int,
    req: AdminUpdateAccountRequest,
    db: Session = Depends(get_db)
):
    """Met à jour les infos d'un compte (nom, téléphone, statut)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    if req.first_name is not None:
        account.first_name = req.first_name.strip()
    if req.last_name is not None:
        account.last_name = req.last_name.strip()
    if req.phone_number is not None:
        account.phone_number = req.phone_number.strip()
    if req.is_active is not None:
        account.is_active = req.is_active

    db.commit()
    db.refresh(account)

    return {
        "status": "success",
        "message": "Compte mis à jour.",
        "account": {
            "id": account.id,
            "email": account.email,
            "first_name": account.first_name,
            "last_name": account.last_name,
            "phone_number": account.phone_number,
            "is_active": account.is_active,
        },
    }


@app.post("/api/admin/accounts/{account_id}/grant-full-access",
          dependencies=[Depends(require_admin_key)])
@app.post("/api/admin/accounts/{account_id}/grant-full-access/",
          dependencies=[Depends(require_admin_key)])
def admin_grant_full_access_account(
    account_id: int,
    req: AdminGrantFullAccessRequest,
    db: Session = Depends(get_db)
):
    """Accorde un accès complet (payant) à un compte."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    plan = db.query(LicensePlan).filter(LicensePlan.code == req.plan_code).first()
    if not plan:
        raise HTTPException(404, f"Plan '{req.plan_code}' introuvable.")

    new_days_float = _duration_to_days_float(req.duration_val, req.duration_unit)

    if req.duration_mode == "add" and account.expires_at:
        now = get_utc_now()
        current_remaining = max(0, (account.expires_at - now).total_seconds() / 86400.0)
        total_days = current_remaining + new_days_float
    else:
        total_days = new_days_float

    account.plan_code = req.plan_code
    account.is_yearly = req.is_yearly
    account.is_trial = False
    account.is_active = True

    if req.reset_expiry:
        account.expires_at = get_utc_now() + timedelta(days=total_days)

    db.commit()
    db.refresh(account)

    return {
        "status": "success",
        "message": f"Accès complet {req.plan_code} accordé.",
        "account_id": account.id,
        "email": account.email,
        "plan_code": account.plan_code,
        "is_yearly": account.is_yearly,
        "expires_at": account.expires_at.isoformat() if account.expires_at else None,
        "duration_days": total_days,
    }


@app.post("/api/admin/accounts/{account_id}/reset-to-trial",
          dependencies=[Depends(require_admin_key)])
@app.post("/api/admin/accounts/{account_id}/reset-to-trial/",
          dependencies=[Depends(require_admin_key)])
def admin_reset_account_to_trial(
    account_id: int,
    req: AdminResetToTrialRequest,
    db: Session = Depends(get_db)
):
    """Remet un compte en mode essai."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    new_days_float = _duration_to_days_float(req.duration_val, req.duration_unit)

    if req.duration_mode == "add" and account.expires_at:
        now = get_utc_now()
        current_remaining = max(0, (account.expires_at - now).total_seconds() / 86400.0)
        total_days = current_remaining + new_days_float
    else:
        total_days = new_days_float

    account.plan_code = "trial"
    account.is_trial = True
    account.is_yearly = False
    account.is_active = True
    account.expires_at = get_utc_now() + timedelta(days=total_days)

    db.commit()
    db.refresh(account)

    return {
        "status": "success",
        "message": "Compte remis en mode essai.",
        "account_id": account.id,
        "email": account.email,
        "plan_code": account.plan_code,
        "is_trial": account.is_trial,
        "expires_at": account.expires_at.isoformat() if account.expires_at else None,
        "duration_days": total_days,
    }


@app.post("/api/admin/accounts/{account_id}/reset-devices",
          dependencies=[Depends(require_admin_key)])
@app.post("/api/admin/accounts/{account_id}/reset-devices/",
          dependencies=[Depends(require_admin_key)])
def admin_reset_account_devices(
    account_id: int,
    db: Session = Depends(get_db)
):
    """Révoque tous les appareils actifs d'un compte."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    now = get_utc_now()
    active_devices = db.query(AccountDevice).filter(
        AccountDevice.account_id == account.id,
        AccountDevice.is_active == True,
    ).all()

    count = len(active_devices)
    for d in active_devices:
        d.is_active = False
        d.revoked_at = now
        d.revoked_reason = "Révoqué par administrateur (reset complet)"

    db.commit()

    return {
        "status": "success",
        "message": f"{count} appareil(s) dissocié(s).",
        "count": count,
    }


@app.delete("/api/admin/accounts/{account_id}",
            dependencies=[Depends(require_admin_key)])
@app.delete("/api/admin/accounts/{account_id}/",
            dependencies=[Depends(require_admin_key)])
def admin_delete_account(
    account_id: int,
    db: Session = Depends(get_db)
):
    """Supprime un compte et tous ses appareils."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    email = account.email

    db.query(AccountDevice).filter(
        AccountDevice.account_id == account.id
    ).delete(synchronize_session=False)

    db.delete(account)
    db.commit()

    return {
        "status": "success",
        "message": f"Compte {email} supprimé.",
    }


# ═══════════════════════════════════════════════════════════
# ROUTES ADMIN — CONFIG DURÉE PAR DÉFAUT
# ═══════════════════════════════════════════════════════════
@app.get("/api/admin/config/default-trial", dependencies=[Depends(require_admin_key)])
@app.get("/api/admin/config/default-trial/", dependencies=[Depends(require_admin_key)])
def get_default_trial_config(db: Session = Depends(get_db)):
    val, unit, days = _get_default_trial_config(db)
    return {
        "duration_val": val,
        "duration_unit": unit,
        "duration_days": days,
    }


@app.put("/api/admin/config/default-trial", dependencies=[Depends(require_admin_key)])
@app.put("/api/admin/config/default-trial/", dependencies=[Depends(require_admin_key)])
def update_default_trial_config(req: DefaultTrialConfigRequest, db: Session = Depends(get_db)):
    _set_default_trial_config(db, req.duration_val, req.duration_unit)
    val, unit, days = _get_default_trial_config(db)
    return {
        "status": "success",
        "message": "Configuration par défaut mise à jour.",
        "duration_val": val,
        "duration_unit": unit,
        "duration_days": days,
    }


# ═══════════════════════════════════════════════════════════
# ACTUALITÉS
# ═══════════════════════════════════════════════════════════
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
        print(f"[NEWS ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur SQL News: {str(err)}")


@app.post("/api/admin/news/create", dependencies=[Depends(require_admin_key)])
@app.post("/api/admin/news/create/", dependencies=[Depends(require_admin_key)])
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
    return {"status": "success", "id": new_article.id, "title": new_article.title}


@app.delete("/api/admin/news/{news_id}", dependencies=[Depends(require_admin_key)])
def delete_admin_news(news_id: int, db: Session = Depends(get_db)):
    item = db.query(AppNews).filter(AppNews.id == news_id).first()
    if not item:
        raise HTTPException(404, "Article introuvable.")
    db.delete(item)
    db.commit()
    return {"status": "success", "message": "Actualité supprimée."}


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
