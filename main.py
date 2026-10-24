# main.py
# ═══════════════════════════════════════════════════════════
#  SmartCollect — Backend API & Admin Server
#  ✅ FastAPI + SQLAlchemy + PostgreSQL
#  ✅ Comptes utilisateurs (email + password)
#  ✅ Multi-appareils par compte (auto-déconnexion)
#  ✅ Plans tarifaires (Basic / Pro / Business)
#  ✅ Upgrade via Play Store
#  ✅ Mot de passe oublié (Brevo SMTP)
# ═══════════════════════════════════════════════════════════

import os
import json
import secrets
import time
import traceback
import re
import hashlib
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

# ═══════════════════════════════════════════════════════════
# CONFIGURATION GLOBALE
# ═══════════════════════════════════════════════════════════
DEFAULT_TRIAL_DAYS = 7
DEFAULT_TRIAL_UNIT = "Jours"
SESSION_DURATION_DAYS = 30

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()

# ─── Brevo SMTP Config ───
BREVO_SMTP_LOGIN = os.getenv("BREVO_SMTP_LOGIN", "").strip()
BREVO_SMTP_KEY = os.getenv("BREVO_SMTP_KEY", "").strip()
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "noreply@smartcollect.com").strip()
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "SmartCollect").strip()

if not ADMIN_API_KEY:
    print("⚠️  [WARN] ADMIN_API_KEY non définie.")

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
# ✅ FONCTION D'ENVOI D'EMAIL (Brevo SMTP)
# ═══════════════════════════════════════════════════════════
def _send_reset_email(to_email: str, otp_code: str) -> bool:
    if not BREVO_SMTP_LOGIN or not BREVO_SMTP_KEY:
        print("⚠️ [EMAIL] BREVO_SMTP_LOGIN ou BREVO_SMTP_KEY non configurés.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Code de réinitialisation - SmartCollect"
        msg["From"] = f"{BREVO_SENDER_NAME} <{BREVO_SENDER_EMAIL}>"
        msg["To"] = to_email

        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
            <h2 style="color: #4F46E5; text-align: center;">SmartCollect</h2>
            <p>Bonjour,</p>
            <p>Vous avez demandé la réinitialisation de votre mot de passe.</p>
            <p>Voici votre code de sécurité :</p>
            <div style="text-align: center; margin: 20px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #0F172A; background: #F1F5F9; padding: 10px 20px; border-radius: 8px; display: inline-block;">{otp_code}</span>
            </div>
            <p>Ce code est valable pendant <strong>15 minutes</strong>.</p>
            <p style="color: #64748B; font-size: 12px;">Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.</p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp-relay.brevo.com", 587) as server:
            server.starttls()
            server.login(BREVO_SMTP_LOGIN, BREVO_SMTP_KEY)
            server.sendmail(BREVO_SENDER_EMAIL, [to_email], msg.as_string())

        print(f"✅ [EMAIL] Envoyé à {to_email}")
        return True
    except Exception as e:
        print(f"❌ [EMAIL ERROR] Échec de l'envoi : {e}")
        return False

# ═══════════════════════════════════════════════════════════
# 🛡️  DÉPENDANCE AUTH ADMIN
# ═══════════════════════════════════════════════════════════
async def require_admin_key(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    if not ADMIN_API_KEY:
        raise HTTPException(503, "Service admin désactivé.")
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
                "code": "trial", "name": "Essai",
                "description": "Essai gratuit 7 jours - 1 tableau, 50 lignes",
                "price_monthly_usd": 0.0, "price_yearly_usd": 0.0,
                "max_tables": 1, "max_rows_per_table": 50,
                "allow_export": False, "allow_cloud_backup": False,
                "allow_bulk_export": False, "allow_merge_tables": False,
                "allow_custom_branding": False, "allow_reminders": False,
                "max_devices": 1, "display_order": 0,
            },
            {
                "code": "basic", "name": "Basic",
                "description": "Pour les indépendants - 10 tableaux, 500 lignes, exports",
                "price_monthly_usd": 5.0, "price_yearly_usd": 40.0,
                "max_tables": 10, "max_rows_per_table": 500,
                "allow_export": True, "allow_cloud_backup": False,
                "allow_bulk_export": False, "allow_merge_tables": False,
                "allow_custom_branding": False, "allow_reminders": False,
                "max_devices": 1, "display_order": 1,
            },
            {
                "code": "pro", "name": "Pro",
                "description": "Pour les PME - Illimité + publipostage + cloud",
                "price_monthly_usd": 10.0, "price_yearly_usd": 80.0,
                "max_tables": 999999, "max_rows_per_table": 999999,
                "allow_export": True, "allow_cloud_backup": True,
                "allow_bulk_export": True, "allow_merge_tables": True,
                "allow_custom_branding": True, "allow_reminders": True,
                "max_devices": 3, "display_order": 2,
            },
            {
                "code": "business", "name": "Business",
                "description": "Pour les organisations - Multi-appareils + support",
                "price_monthly_usd": 25.0, "price_yearly_usd": 200.0,
                "max_tables": 999999, "max_rows_per_table": 999999,
                "allow_export": True, "allow_cloud_backup": True,
                "allow_bulk_export": True, "allow_merge_tables": True,
                "allow_custom_branding": True, "allow_reminders": True,
                "max_devices": 5, "display_order": 3,
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
    if "minute" in u or u == "min" or u == "mn": return val / (24 * 60)
    if "heure" in u or u == "h" or u == "hr" or u == "hrs": return val / 24
    if "jour" in u or u == "day" or u == "days" or u == "j": return float(val)
    if "mois" in u or u == "month" or u == "months": return float(val * 30)
    if "an" in u or u == "year" or u == "years": return float(val * 365)
    return float(val)

def _duration_to_timedelta(val: int, unit: str) -> timedelta:
    u = (unit or "").lower().strip()
    if "minute" in u or u == "min" or u == "mn": return timedelta(minutes=val)
    if "heure" in u or u == "h" or u == "hr" or u == "hrs": return timedelta(hours=val)
    if "jour" in u or u == "day" or u == "days" or u == "j": return timedelta(days=val)
    if "mois" in u or u == "month" or u == "months": return timedelta(days=val * 30)
    if "an" in u or u == "year" or u == "years": return timedelta(days=val * 365)
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
        try: return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except Exception: return False
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
    if plan: return max(1, plan.max_devices or 1)
    fallbacks = {"trial": 1, "basic": 1, "pro": 3, "business": 5}
    return fallbacks.get(plan_code, 1)

def _get_plan_limits(plan_code: str, db: Session) -> dict:
    plan = db.query(LicensePlan).filter(LicensePlan.code == plan_code).first()
    if plan:
        return {
            "max_tables": plan.max_tables, "max_rows_per_table": plan.max_rows_per_table,
            "allow_export": bool(plan.allow_export), "allow_cloud_backup": bool(plan.allow_cloud_backup),
            "allow_bulk_export": bool(plan.allow_bulk_export), "allow_merge_tables": bool(plan.allow_merge_tables),
            "allow_custom_branding": bool(plan.allow_custom_branding), "allow_reminders": bool(plan.allow_reminders),
            "max_devices": plan.max_devices,
        }
    return {
        "max_tables": 1 if plan_code == "trial" else 999999,
        "max_rows_per_table": 50 if plan_code == "trial" else 999999,
        "allow_export": plan_code != "trial", "allow_cloud_backup": plan_code in ("pro", "business"),
        "allow_bulk_export": plan_code in ("pro", "business"), "allow_merge_tables": plan_code in ("pro", "business"),
        "allow_custom_branding": plan_code in ("pro", "business"), "allow_reminders": plan_code in ("pro", "business"),
        "max_devices": {"trial": 1, "basic": 1, "pro": 3, "business": 5}.get(plan_code, 1),
    }

def _enforce_device_limit(account: Account, device_id: str, device_name: str, platform: str, app_version: str, db: Session) -> AccountDevice:
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
    defaults = {"default_trial_val": "7", "default_trial_unit": "Jours"}
    results = {}
    for k in defaults:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        results[k] = row.value if row else defaults[k]

    try: val = int(results["default_trial_val"])
    except ValueError: val = 7
    unit = results["default_trial_unit"] or "Jours"
    days = _duration_to_days_float(val, unit)
    return val, unit, days

def _set_default_trial_config(db: Session, val: int, unit: str):
    for k, v in [("default_trial_val", str(val)), ("default_trial_unit", unit)]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row: row.value = v
        else: db.add(AppConfig(key=k, value=v))
    db.commit()

# ═══════════════════════════════════════════════════════════
# APPLICATION FASTAPI
# ═══════════════════════════════════════════════════════════
app = FastAPI(title="SmartCollect API & Admin Server", redirect_slashes=True)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["*"],
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

class AdminUpdateAccountRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    plan_code: Optional[str] = Field(None, pattern="^(trial|basic|pro|business)$")
    is_yearly: Optional[bool] = None
    is_trial: Optional[bool] = None
    is_active: Optional[bool] = None

class AdminGrantFull
