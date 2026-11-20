# main.py
# ═══════════════════════════════════════════════════════════
#  SmartCollect — Backend API & Admin Server
#  ✅ Email via Brevo API HTTP (port 443, jamais bloqué)
# ═══════════════════════════════════════════════════════════

import os
import json
import secrets
import time
import traceback
import re
import hashlib
import hmac
import base64
import random
import string
import csv
import io
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

import play_billing
from privacy import delete_account_page_html, privacy_page
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Text, Float, text,
    ForeignKey, Index, func, or_, and_
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
    print("⚠️  [WARN] bcrypt non installé.")

# ═══════════════════════════════════════════════════════════
# CONFIGURATION GLOBALE
# ═══════════════════════════════════════════════════════════
DEFAULT_TRIAL_DAYS = 7
DEFAULT_TRIAL_UNIT = "Jours"
SESSION_DURATION_DAYS = 30

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()

# Secret used to derive each account's backup encryption key. Set once in
# the hosting environment and NEVER change it: backups made with a key
# derived from the old secret could no longer be restored.
BACKUP_MASTER_SECRET = os.getenv("BACKUP_MASTER_SECRET", "").strip()
BACKUP_KEY_VERSION = 1

# ─── Brevo API Config ───
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "noreply@smartcollect.com").strip()
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "SmartCollect").strip()

if not ADMIN_API_KEY:
    print("⚠️  [WARN] ADMIN_API_KEY non définie.")

# ═══════════════════════════════════════════════════════════
# CONFIGURATION BASE DE DONNÉES
# ═══════════════════════════════════════════════════════════
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
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
# ✅ FONCTION D'ENVOI D'EMAIL (Brevo API HTTP - Port 443)
# ═══════════════════════════════════════════════════════════
def _send_reset_email(to_email: str, otp_code: str) -> bool:
    if not BREVO_API_KEY:
        print("⚠️ [EMAIL] BREVO_API_KEY non configurée sur Render.")
        return False

    try:
        res = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": BREVO_API_KEY,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
                "to": [{"email": to_email}],
                "subject": "Code de réinitialisation - SmartCollect",
                "htmlContent": f"""
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
            },
            timeout=15
        )

        if res.status_code in (200, 201, 202):
            print(f"✅ [EMAIL] Envoyé à {to_email} (via API HTTP)")
            return True
        else:
            print(f"❌ [EMAIL ERROR] Brevo API a refusé : {res.status_code} - {res.text}")
            return False
    except Exception as e:
        print(f"❌ [EMAIL ERROR] Exception : {e}")
        return False

# ═══════════════════════════════════════════════════════════
# 🛡️  DÉPENDANCE AUTH ADMIN
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# ADMIN : session, protection, journal, aides
# ═══════════════════════════════════════════════════════════
ADMIN_SESSION_HOURS = 8
_ADMIN_SIGN_KEY = hashlib.sha256(("smartcollect-admin-session:" + ADMIN_API_KEY).encode()).digest()
_ADMIN_FAIL_WINDOW_SEC = 15 * 60
_ADMIN_FAIL_MAX = 8
_admin_fail_store: dict = {}

def _client_ip(request: Request) -> str:
    # Behind the host's proxy the LAST forwarded address is the one the
    # proxy itself saw; earlier ones can be forged by the caller.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd: return fwd.split(",")[-1].strip()[:64]
    return (request.client.host if request.client else "?")[:64]

def _admin_check_blocked(ip: str):
    now = time.time()
    hist = [t for t in _admin_fail_store.get(ip, []) if now - t < _ADMIN_FAIL_WINDOW_SEC]
    _admin_fail_store[ip] = hist
    if len(hist) >= _ADMIN_FAIL_MAX:
        raise HTTPException(429, "Trop d'essais. Réessayez dans quelques minutes.")

def _admin_register_failure(ip: str):
    _admin_fail_store.setdefault(ip, []).append(time.time())

def _admin_make_token():
    exp = int(time.time()) + ADMIN_SESSION_HOURS * 3600
    payload = f"{exp}.{secrets.token_urlsafe(8)}"
    sig = hmac.new(_ADMIN_SIGN_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}", exp

def _admin_token_valid(token: str) -> bool:
    try:
        exp_s, nonce, sig = token.split(".")
        good = hmac.new(_ADMIN_SIGN_KEY, f"{exp_s}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp_s) > time.time()
    except Exception:
        return False

async def require_admin_key(request: Request, x_admin_key: str = Header(None, alias="X-Admin-Key"), authorization: str = Header(None)):
    """Admin access: either the key itself (X-Admin-Key, used by scripts and
    the desktop tool) or a session token from /api/admin/session (Bearer,
    used by the web console)."""
    if not ADMIN_API_KEY:
        raise HTTPException(503, "Service admin désactivé.")
    ip = _client_ip(request)
    _admin_check_blocked(ip)
    if x_admin_key and hmac.compare_digest(x_admin_key.strip().encode(), ADMIN_API_KEY.encode()):
        return True
    if authorization and authorization.lower().startswith("bearer ") and _admin_token_valid(authorization[7:].strip()):
        return True
    _admin_register_failure(ip)
    raise HTTPException(401, "Clé admin invalide ou manquante.")


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
    # Admin console / subscriptions (all optional)
    admin_note = Column(Text, nullable=True)
    grant_source = Column(String(20), nullable=True)  # admin | trial | play_store
    subscription_state = Column(String(30), nullable=True)  # filled by Play Billing later
    auto_renewing = Column(Boolean, nullable=True)
    purchase_order_id = Column(String(100), nullable=True)
    purchased_at = Column(DateTime, nullable=True)
    # Company accounts: the plan, the end date and the seat count come from
    # the organization; the owner adds and removes the employees.
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    org_role = Column(String(10), nullable=True)  # owner | member

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    plan_code = Column(String(20), default="enterprise")
    seats = Column(Integer, default=5)  # employees allowed (the owner counts)
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    admin_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

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

class AdminAudit(Base):
    __tablename__ = "admin_audit"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=get_utc_now, index=True)
    action = Column(String(60), nullable=False, index=True)
    target_type = Column(String(20), default="account")
    target_id = Column(Integer, nullable=True, index=True)
    target_label = Column(String(255), default="")
    details = Column(Text, default="")
    ip = Column(String(64), default="")

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
    ("ALTER accounts.admin_note", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS admin_note TEXT"),
    ("ALTER accounts.grant_source", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS grant_source VARCHAR(20)"),
    ("ALTER accounts.subscription_state", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS subscription_state VARCHAR(30)"),
    ("ALTER accounts.auto_renewing", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS auto_renewing BOOLEAN"),
    ("ALTER accounts.purchase_order_id", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS purchase_order_id VARCHAR(100)"),
    ("ALTER accounts.purchased_at", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS purchased_at TIMESTAMP"),
    ("CREATE TABLE organizations IF NOT EXISTS",
     "CREATE TABLE IF NOT EXISTS organizations ("
     "  id SERIAL PRIMARY KEY,"
     "  name VARCHAR(150) NOT NULL,"
     "  plan_code VARCHAR(20) DEFAULT 'enterprise',"
     "  seats INTEGER DEFAULT 5,"
     "  expires_at TIMESTAMP,"
     "  is_active BOOLEAN DEFAULT TRUE,"
     "  admin_note TEXT,"
     "  created_at TIMESTAMP DEFAULT NOW(),"
     "  updated_at TIMESTAMP DEFAULT NOW()"
     ")"),
    ("ALTER accounts.organization_id", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL"),
    ("ALTER accounts.org_role", "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS org_role VARCHAR(10)"),
    ("Plan business -> enterprise (hidden, given by the admin)",
     "UPDATE license_plans SET code = 'enterprise', name = 'Entreprise', description = 'Sur devis - postes à la carte', is_active = FALSE WHERE code = 'business' AND NOT EXISTS (SELECT 1 FROM license_plans WHERE code = 'enterprise')"),
    ("Accounts business -> enterprise", "UPDATE accounts SET plan_code = 'enterprise' WHERE plan_code = 'business'"),
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
            {"code": "trial", "name": "Essai", "description": "Essai gratuit 7 jours - 1 tableau, 50 lignes", "price_monthly_usd": 0.0, "price_yearly_usd": 0.0, "max_tables": 1, "max_rows_per_table": 50, "allow_export": False, "allow_cloud_backup": False, "allow_bulk_export": False, "allow_merge_tables": False, "allow_custom_branding": False, "allow_reminders": False, "max_devices": 1, "display_order": 0},
            {"code": "basic", "name": "Basic", "description": "Pour les indépendants - 10 tableaux, 500 lignes, exports", "price_monthly_usd": 5.0, "price_yearly_usd": 40.0, "max_tables": 10, "max_rows_per_table": 500, "allow_export": True, "allow_cloud_backup": False, "allow_bulk_export": False, "allow_merge_tables": False, "allow_custom_branding": False, "allow_reminders": False, "max_devices": 1, "display_order": 1},
            {"code": "pro", "name": "Pro", "description": "Pour les PME - Illimité + publipostage + cloud", "price_monthly_usd": 10.0, "price_yearly_usd": 80.0, "max_tables": 999999, "max_rows_per_table": 999999, "allow_export": True, "allow_cloud_backup": True, "allow_bulk_export": True, "allow_merge_tables": True, "allow_custom_branding": True, "allow_reminders": True, "max_devices": 3, "display_order": 2},
            {"code": "enterprise", "name": "Entreprise", "description": "Sur devis - postes à la carte", "price_monthly_usd": 0.0, "price_yearly_usd": 0.0, "max_tables": 999999, "max_rows_per_table": 999999, "allow_export": True, "allow_cloud_backup": True, "allow_bulk_export": True, "allow_merge_tables": True, "allow_custom_branding": True, "allow_reminders": True, "max_devices": 2, "display_order": 3, "is_active": False},
        ]
        for p in defaults:
            existing = db.query(LicensePlan).filter(LicensePlan.code == p["code"]).first()
            if not existing:
                db.add(LicensePlan(**p))
        db.commit()
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
# HELPERS
# ═══════════════════════════════════════════════════════════
def _duration_to_days_float(val: int, unit: str) -> float:
    u = (unit or "").lower().strip()
    if "minute" in u: return val / (24 * 60)
    if "heure" in u or u == "h": return val / 24
    if "jour" in u or u == "j": return float(val)
    if "mois" in u: return float(val * 30)
    if "an" in u: return float(val * 365)
    return float(val)

def _duration_to_timedelta(val: int, unit: str) -> timedelta:
    u = (unit or "").lower().strip()
    if "minute" in u: return timedelta(minutes=val)
    if "heure" in u or u == "h": return timedelta(hours=val)
    if "jour" in u or u == "j": return timedelta(days=val)
    if "mois" in u: return timedelta(days=val * 30)
    if "an" in u: return timedelta(days=val * 365)
    return timedelta(days=val)

def _hash_password(password: str) -> str:
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    return hashlib.sha256(f"smartcollect:{password}".encode()).hexdigest()

def _verify_password(password: str, password_hash: str) -> bool:
    if BCRYPT_AVAILABLE:
        try: return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except Exception: return False
    return hashlib.sha256(f"smartcollect:{password}".encode()).hexdigest() == password_hash

def _generate_session_token() -> str:
    return secrets.token_urlsafe(64)

def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()

def _validate_email(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))

def _get_plan_max_devices(plan_code: str, db: Session) -> int:
    plan = db.query(LicensePlan).filter(LicensePlan.code == plan_code).first()
    if plan: return max(1, plan.max_devices or 1)
    return {"trial": 1, "basic": 1, "pro": 3, "enterprise": 2}.get(plan_code, 1)

def _get_plan_limits(plan_code: str, db: Session) -> dict:
    plan = db.query(LicensePlan).filter(LicensePlan.code == plan_code).first()
    if plan:
        return {"max_tables": plan.max_tables, "max_rows_per_table": plan.max_rows_per_table, "allow_export": bool(plan.allow_export), "allow_cloud_backup": bool(plan.allow_cloud_backup), "allow_bulk_export": bool(plan.allow_bulk_export), "allow_merge_tables": bool(plan.allow_merge_tables), "allow_custom_branding": bool(plan.allow_custom_branding), "allow_reminders": bool(plan.allow_reminders), "max_devices": plan.max_devices}
    return {"max_tables": 1 if plan_code == "trial" else 999999, "max_rows_per_table": 50 if plan_code == "trial" else 999999, "allow_export": plan_code != "trial", "allow_cloud_backup": plan_code in ("pro", "enterprise"), "allow_bulk_export": plan_code in ("pro", "enterprise"), "allow_merge_tables": plan_code in ("pro", "enterprise"), "allow_custom_branding": plan_code in ("pro", "enterprise"), "allow_reminders": plan_code in ("pro", "enterprise"), "max_devices": {"trial": 1, "basic": 1, "pro": 3, "enterprise": 2}.get(plan_code, 1)}

def _enforce_device_limit(account: Account, device_id: str, device_name: str, platform: str, app_version: str, db: Session) -> AccountDevice:
    clean_device = (device_id or "").strip().upper()
    max_devices = _get_plan_max_devices(account.plan_code, db)
    existing = db.query(AccountDevice).filter(AccountDevice.account_id == account.id, AccountDevice.device_id == clean_device).first()
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
    active_devices = db.query(AccountDevice).filter(AccountDevice.account_id == account.id, AccountDevice.is_active == True).order_by(AccountDevice.last_seen_at.desc()).all()
    while len(active_devices) >= max_devices:
        oldest = active_devices.pop()
        oldest.is_active = False
        oldest.revoked_at = get_utc_now()
        oldest.revoked_reason = f"Auto-déconnecté (limite {max_devices} appareils atteinte)"
    new_device = AccountDevice(account_id=account.id, device_id=clean_device, device_name=device_name or "Appareil sans nom", platform=platform or "unknown", app_version=app_version or "", session_token=_generate_session_token(), session_expires_at=get_utc_now() + timedelta(days=SESSION_DURATION_DAYS), is_active=True)
    db.add(new_device)
    return new_device

def _org_of(account: Account, db: Session):
    return db.query(Organization).filter(Organization.id == account.organization_id).first() if account.organization_id else None

def _org_seats_used(org: Organization, db: Session) -> int:
    return db.query(func.count(Account.id)).filter(Account.organization_id == org.id).scalar() or 0

def _sync_org_members(org: Organization, db: Session):
    """The members follow the organization: plan, end date, paid, active."""
    for m in db.query(Account).filter(Account.organization_id == org.id).all():
        m.plan_code = org.plan_code
        m.is_trial = False
        m.expires_at = org.expires_at
        m.grant_source = "org"
        if not org.is_active: m.is_active = False

def _org_payload(org: Organization, account: Account, db: Session) -> dict:
    used = _org_seats_used(org, db)
    return {"id": org.id, "name": org.name, "role": account.org_role or "member", "seats": org.seats, "used": used, "full": used >= org.seats}

def _account_to_access_payload(account: Account, db: Session) -> dict:
    plan_code = account.plan_code or "trial"
    limits = _get_plan_limits(plan_code, db)
    plan = db.query(LicensePlan).filter(LicensePlan.code == plan_code).first()
    expired = bool(account.expires_at and get_utc_now() > account.expires_at)
    org = _org_of(account, db)
    return {"organization": _org_payload(org, account, db) if org else None, "mode": "expired" if expired else ("trial" if account.is_trial else "full"), "expired": expired, "is_trial": bool(account.is_trial), "plan_code": plan_code, "plan_name": plan.name if plan else plan_code.capitalize(), "expires_at": account.expires_at.isoformat() if account.expires_at else None, "max_tables": limits["max_tables"], "max_rows_per_table": limits["max_rows_per_table"], "allow_export": limits["allow_export"], "allow_cloud_backup": limits["allow_cloud_backup"], "allow_bulk_export": limits["allow_bulk_export"], "allow_merge_tables": limits["allow_merge_tables"], "allow_custom_branding": limits["allow_custom_branding"], "allow_reminders": limits["allow_reminders"], "max_devices": limits["max_devices"]}

def _get_default_trial_config(db: Session) -> tuple:
    defaults = {"default_trial_val": "7", "default_trial_unit": "Jours"}
    results = {}
    for k in defaults:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        results[k] = row.value if row else defaults[k]
    try: val = int(results["default_trial_val"])
    except ValueError: val = 7
    unit = results["default_trial_unit"] or "Jours"
    return val, unit, _duration_to_days_float(val, unit)

def _set_default_trial_config(db: Session, val: int, unit: str):
    for k, v in [("default_trial_val", str(val)), ("default_trial_unit", unit)]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row: row.value = v
        else: db.add(AppConfig(key=k, value=v))
    db.commit()

# ═══════════════════════════════════════════════════════════
# ADMIN : aides données (statut, recherche, journal)
# ═══════════════════════════════════════════════════════════
def _iso(dt):
    """Dates are stored as naive UTC: the console needs the explicit Z."""
    return dt.isoformat() + "Z" if dt else None

def _mask_email(email: str) -> str:
    """Kept in the audit log instead of the full address, so deleting an
    account does not leave its e-mail behind."""
    try:
        local, domain = email.split("@", 1)
        return f"{local[:2]}***@{domain}"
    except Exception:
        return "***"

def _csv_safe(value):
    """Spreadsheet formula injection: a cell starting with = + - @ would be
    executed by Excel."""
    s = str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s

def _account_status(acc: Account, now) -> str:
    if not acc.is_active: return "disabled"
    if acc.expires_at and acc.expires_at < now: return "expired"
    return "trial" if acc.is_trial else "paid"

def _account_source(acc: Account) -> str:
    """Where the current access comes from: 'play_store' (a purchase),
    'admin' (granted by hand) or 'trial'."""
    if acc.grant_source: return acc.grant_source
    if acc.purchase_token: return "play_store"
    return "trial" if acc.is_trial else "admin"

def _account_dict(acc: Account, now, devices: int = 0) -> dict:
    days_left = None
    if acc.expires_at: days_left = round((acc.expires_at - now).total_seconds() / 86400.0, 2)
    return {"id": acc.id, "email": acc.email, "first_name": acc.first_name or "", "last_name": acc.last_name or "", "phone_number": acc.phone_number or "", "plan_code": acc.plan_code or "trial", "is_yearly": bool(acc.is_yearly), "is_trial": bool(acc.is_trial), "is_active": bool(acc.is_active), "status": _account_status(acc, now), "source": _account_source(acc), "expires_at": _iso(acc.expires_at), "days_left": days_left, "created_at": _iso(acc.created_at), "last_login_at": _iso(acc.last_login_at), "last_login_device": acc.last_login_device or "", "active_devices": devices, "has_purchase": bool(acc.purchase_token), "purchase_token": acc.purchase_token, "purchase_platform": acc.purchase_platform or "", "admin_note": acc.admin_note or "", "subscription_state": acc.subscription_state or "", "auto_renewing": acc.auto_renewing, "purchase_order_id": acc.purchase_order_id or "", "purchased_at": _iso(acc.purchased_at)}

def _device_counts(db: Session, ids: list) -> dict:
    if not ids: return {}
    rows = db.query(AccountDevice.account_id, func.count(AccountDevice.id)).filter(AccountDevice.account_id.in_(ids), AccountDevice.is_active == True).group_by(AccountDevice.account_id).all()
    return {r[0]: r[1] for r in rows}

def _filtered_accounts(db: Session, q: str, status: str, plan: str, now):
    query = db.query(Account)
    q = (q or "").strip()
    if q:
        esc = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        conds = [func.lower(Account.email).like(like, escape="\\"), func.lower(Account.first_name).like(like, escape="\\"), func.lower(Account.last_name).like(like, escape="\\"), Account.phone_number.like(like, escape="\\")]
        if q.isdigit(): conds.append(Account.id == int(q))
        query = query.filter(or_(*conds))
    if plan: query = query.filter(Account.plan_code == plan)
    live = and_(Account.is_active == True, or_(Account.expires_at == None, Account.expires_at >= now))
    if status == "trial": query = query.filter(live, Account.is_trial == True)
    elif status == "paid": query = query.filter(live, Account.is_trial == False)
    elif status == "expired": query = query.filter(Account.is_active == True, Account.expires_at != None, Account.expires_at < now)
    elif status == "disabled": query = query.filter(Account.is_active == False)
    elif status == "expiring": query = query.filter(live, Account.expires_at != None, Account.expires_at <= now + timedelta(days=7))
    elif status == "admin": query = query.filter(live, Account.is_trial == False, Account.purchase_token == None)
    return query

def _purge_reset_codes(db: Session, email: str):
    """Password-reset codes are stored per e-mail in app_config: remove them
    with the account so no trace of the address stays behind."""
    e = _normalize_email(email)
    db.query(AppConfig).filter(AppConfig.key.in_([f"reset_code_{e}", f"reset_expires_{e}"])).delete(synchronize_session=False)

def _label(db: Session, account_id: int) -> str:
    a = db.query(Account).filter(Account.id == account_id).first()
    return _mask_email(a.email) if a else f"#{account_id}"

def _audit(db: Session, request, action: str, target_id=None, label: str = "", details=None, target_type: str = "account"):
    """Journal of admin actions. Never breaks the action it records."""
    try:
        db.add(AdminAudit(action=action, target_type=target_type, target_id=target_id, target_label=(label or "")[:255], details=json.dumps(details, ensure_ascii=False, default=str) if details else "", ip=_client_ip(request) if request is not None else ""))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[audit] échec : {e}")

def _audit_dict(a: AdminAudit) -> dict:
    try: details = json.loads(a.details) if a.details else None
    except Exception: details = None
    return {"id": a.id, "at": _iso(a.created_at), "action": a.action, "target_type": a.target_type, "target_id": a.target_id, "target_label": a.target_label or "", "details": details, "ip": a.ip or ""}

# ═══════════════════════════════════════════════════════════
# APPLICATION FASTAPI & SCHÉMAS
# ═══════════════════════════════════════════════════════════
app = FastAPI(title="SmartCollect API", redirect_slashes=True)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["*"])

class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)

class ResetPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    otp_code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=100)

class UpdatePlanRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price_monthly_usd: Optional[float] = None
    price_yearly_usd: Optional[float] = None
    max_tables: Optional[int] = None
    max_rows_per_table: Optional[int] = None
    allow_export: Optional[bool] = None
    allow_cloud_backup: Optional[bool] = None
    allow_bulk_export: Optional[bool] = None
    allow_merge_tables: Optional[bool] = None
    allow_custom_branding: Optional[bool] = None
    allow_reminders: Optional[bool] = None
    max_devices: Optional[int] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None

class NewsCreateRequest(BaseModel):
    title: str
    summary: str
    content: Optional[str] = ""
    category: str = "news"
    version: Optional[str] = None
    download_url: Optional[str] = None

class DefaultTrialConfigRequest(BaseModel):
    duration_val: int
    duration_unit: str

class RegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str = ""
    last_name: str = ""
    phone_number: str = ""
    device_id: str
    device_name: str = ""
    platform: str = "unknown"
    app_version: str = ""

class LoginRequest(BaseModel):
    email: str
    password: str
    device_id: str
    device_name: str = ""
    platform: str = "unknown"
    app_version: str = ""

class VerifySessionRequest(BaseModel):
    session_token: str
    device_id: str

class LogoutRequest(BaseModel):
    session_token: str

class AccountUpgradeRequest(BaseModel):
    session_token: str
    plan_code: str
    is_yearly: bool = False
    purchase_token: Optional[str] = None
    purchase_platform: str = "play_store"

class RevokeDeviceRequest(BaseModel):
    session_token: str
    device_id_to_revoke: str

class AdminUpdateAccountRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    plan_code: Optional[str] = None
    is_yearly: Optional[bool] = None
    is_trial: Optional[bool] = None
    is_active: Optional[bool] = None

class AdminGrantFullAccessRequest(BaseModel):
    plan_code: str
    is_yearly: bool = False
    duration_val: int
    duration_unit: str
    duration_mode: str = "replace"
    reset_expiry: bool = True

class AdminResetToTrialRequest(BaseModel):
    duration_val: int
    duration_unit: str
    duration_mode: str = "replace"

class AdminSessionRequest(BaseModel):
    key: str = Field(..., max_length=500)

class AdminNoteRequest(BaseModel):
    note: str = ""

class AdminExtendRequest(BaseModel):
    days: float

class AdminBulkRequest(BaseModel):
    ids: List[int]
    action: str
    days: Optional[float] = None

# ═══════════════════════════════════════════════════════════
# ROUTES AUTH
# ═══════════════════════════════════════════════════════════
@app.post("/api/auth/register")
def auth_register(req: RegisterRequest, db: Session = Depends(get_db)):
    try:
        email = _normalize_email(req.email)
        if not _validate_email(email) or len(req.password) < 8:
            raise HTTPException(400, "Email invalide ou mot de passe trop court.")
        if db.query(Account).filter(Account.email == email).first():
            raise HTTPException(409, "Email déjà utilisé.")
        def_val, def_unit, _ = _get_default_trial_config(db)
        now = get_utc_now()
        new_account = Account(email=email, password_hash=_hash_password(req.password), first_name=req.first_name.strip(), last_name=req.last_name.strip(), phone_number=req.phone_number.strip(), plan_code="trial", is_trial=True, is_active=True, expires_at=now + _duration_to_timedelta(def_val, def_unit), created_at=now, last_login_at=now, last_login_device=req.device_id.strip().upper())
        db.add(new_account)
        db.flush()
        device = _enforce_device_limit(new_account, req.device_id, req.device_name, req.platform, req.app_version, db)
        db.commit()
        db.refresh(new_account)
        db.refresh(device)
        return {"status": "success", "message": "Compte créé.", "account": {"id": new_account.id, "email": new_account.email, "first_name": new_account.first_name, "last_name": new_account.last_name, "plan_code": new_account.plan_code, "is_trial": new_account.is_trial, "expires_at": new_account.expires_at.isoformat() if new_account.expires_at else None}, "session_token": device.session_token, "session_expires_at": device.session_expires_at.isoformat(), "access": _account_to_access_payload(new_account, db)}
    except HTTPException: raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))

@app.post("/api/auth/login")
def auth_login(req: LoginRequest, db: Session = Depends(get_db)):
    try:
        email = _normalize_email(req.email)
        check_rate_limit(f"login:{email}")
        account = db.query(Account).filter(Account.email == email).first()
        if not account or not account.is_active:
            raise HTTPException(401, "Email ou mot de passe incorrect.")
        if not _verify_password(req.password, account.password_hash):
            raise HTTPException(401, "Email ou mot de passe incorrect.")
        now = get_utc_now()
        # Expired accounts can still sign in: the app opens read-only and
        # offers the subscription (access.expired = true).
        device = _enforce_device_limit(account, req.device_id, req.device_name, req.platform, req.app_version, db)
        account.last_login_at = now
        account.last_login_device = req.device_id.strip().upper()
        db.commit()
        db.refresh(account)
        db.refresh(device)
        return {"status": "success", "message": "Connexion réussie.", "account": {"id": account.id, "email": account.email, "first_name": account.first_name, "last_name": account.last_name, "plan_code": account.plan_code, "is_trial": account.is_trial, "expires_at": account.expires_at.isoformat() if account.expires_at else None}, "session_token": device.session_token, "session_expires_at": device.session_expires_at.isoformat(), "access": _account_to_access_payload(account, db)}
    except HTTPException: raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))

@app.post("/api/auth/forgot-password")
def auth_forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = _normalize_email(req.email)
    account = db.query(Account).filter(Account.email == email).first()
    if account:
        otp_code = ''.join(random.choices(string.digits, k=6))
        expires_at = get_utc_now() + timedelta(minutes=15)
        def set_config(key, val):
            row = db.query(AppConfig).filter(AppConfig.key == key).first()
            if row: row.value = val
            else: db.add(AppConfig(key=key, value=val))
        set_config(f"reset_code_{email}", otp_code)
        set_config(f"reset_expires_{email}", expires_at.isoformat())
        db.commit()
        _send_reset_email(email, otp_code)
        print(f"🔑 [RESET PASSWORD] Code pour {email} : {otp_code}")
    return {"status": "success", "message": "Si cet email existe, un code a été envoyé."}

@app.post("/api/auth/reset-password")
def auth_reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    email = _normalize_email(req.email)
    account = db.query(Account).filter(Account.email == email).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    code_row = db.query(AppConfig).filter(AppConfig.key == f"reset_code_{email}").first()
    exp_row = db.query(AppConfig).filter(AppConfig.key == f"reset_expires_{email}").first()
    if not code_row or code_row.value != req.otp_code: raise HTTPException(400, "Code invalide.")
    if not exp_row: raise HTTPException(400, "Code expiré.")
    try:
        if get_utc_now() > datetime.fromisoformat(exp_row.value): raise HTTPException(400, "Code expiré.")
    except ValueError: raise HTTPException(400, "Erreur date.")
    account.password_hash = _hash_password(req.new_password)
    db.delete(code_row)
    db.delete(exp_row)
    db.query(AccountDevice).filter(AccountDevice.account_id == account.id).update({"is_active": False})
    db.commit()
    return {"status": "success", "message": "Mot de passe mis à jour."}

@app.post("/api/auth/verify-session")
def auth_verify_session(req: VerifySessionRequest, db: Session = Depends(get_db)):
    try:
        clean_device = req.device_id.strip().upper()
        now = get_utc_now()
        device = db.query(AccountDevice).filter(AccountDevice.session_token == req.session_token, AccountDevice.device_id == clean_device).first()
        if not device: raise HTTPException(401, "Session introuvable.")
        if not device.is_active: raise HTTPException(403, "Session révoquée.")
        if device.session_expires_at and now > device.session_expires_at: raise HTTPException(401, "Session expirée.")
        account = db.query(Account).filter(Account.id == device.account_id).first()
        if not account or not account.is_active: raise HTTPException(403, "Compte désactivé.")
        device.last_seen_at = now
        db.commit()
        db.refresh(account)
        return {"status": "valid", "account": {"id": account.id, "email": account.email, "first_name": account.first_name, "last_name": account.last_name, "plan_code": account.plan_code, "is_trial": account.is_trial, "expires_at": account.expires_at.isoformat() if account.expires_at else None}, "access": _account_to_access_payload(account, db)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e))

@app.post("/api/auth/logout")
def auth_logout(req: LogoutRequest, db: Session = Depends(get_db)):
    try:
        device = db.query(AccountDevice).filter(AccountDevice.session_token == req.session_token).first()
        if device:
            device.is_active = False
            device.revoked_at = get_utc_now()
            device.revoked_reason = "Déconnexion manuelle"
            db.commit()
        return {"status": "success", "message": "Déconnecté."}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))

# ═══════════════════════════════════════════════════════════
# ROUTES PUBLIQUES
# ═══════════════════════════════════════════════════════════
@app.get("/")
def home(): return {"status": "online", "database": "PostgreSQL", "service": "SmartCollect API", "default_trial_days": DEFAULT_TRIAL_DAYS}

@app.get("/health")
def health(): return {"status": "healthy"}

@app.get("/privacy", response_class=HTMLResponse)
def privacy(lang: str = "fr"):
    """Public privacy policy of the app (Google Play, Google sign-in)."""
    return privacy_page(lang)

@app.get("/api/plans")
def get_public_plans(db: Session = Depends(get_db)):
    plans = db.query(LicensePlan).filter(LicensePlan.is_active == True).order_by(LicensePlan.display_order.asc()).all()
    return [{"code": p.code, "name": p.name, "description": p.description or "", "price_monthly_usd": float(p.price_monthly_usd or 0), "price_yearly_usd": float(p.price_yearly_usd or 0), "discount_yearly_pct": round((1 - (p.price_yearly_usd / (float(p.price_monthly_usd or 0) * 12))) * 100, 1) if p.price_monthly_usd and p.price_yearly_usd else 0.0, "max_tables": p.max_tables, "max_rows_per_table": p.max_rows_per_table, "allow_export": bool(p.allow_export), "allow_cloud_backup": bool(p.allow_cloud_backup), "allow_bulk_export": bool(p.allow_bulk_export), "allow_merge_tables": bool(p.allow_merge_tables), "allow_custom_branding": bool(p.allow_custom_branding), "allow_reminders": bool(p.allow_reminders), "max_devices": p.max_devices} for p in plans]

@app.get("/api/plans/{code}")
def get_plan_by_code(code: str, db: Session = Depends(get_db)):
    plan = db.query(LicensePlan).filter(LicensePlan.code == code.lower()).first()
    if not plan: raise HTTPException(404, "Plan introuvable.")
    return {"code": plan.code, "name": plan.name, "description": plan.description or "", "price_monthly_usd": float(plan.price_monthly_usd or 0), "price_yearly_usd": float(plan.price_yearly_usd or 0), "max_tables": plan.max_tables, "max_rows_per_table": plan.max_rows_per_table, "max_devices": plan.max_devices, "allow_export": bool(plan.allow_export), "allow_cloud_backup": bool(plan.allow_cloud_backup), "allow_bulk_export": bool(plan.allow_bulk_export), "allow_merge_tables": bool(plan.allow_merge_tables), "allow_custom_branding": bool(plan.allow_custom_branding), "allow_reminders": bool(plan.allow_reminders), "is_active": bool(plan.is_active)}

@app.get("/api/config/default-trial")
def get_public_default_trial(db: Session = Depends(get_db)):
    val, unit, days = _get_default_trial_config(db)
    return {"duration_val": val, "duration_unit": unit, "duration_days": days}

# ═══════════════════════════════════════════════════════════
# ROUTES DEVICES & UPGRADE
# ═══════════════════════════════════════════════════════════
@app.get("/api/account/devices")
def get_account_devices(session_token: str, db: Session = Depends(get_db)):
    device = db.query(AccountDevice).filter(AccountDevice.session_token == session_token).first()
    if not device or not device.is_active: raise HTTPException(401, "Session invalide.")
    account = db.query(Account).filter(Account.id == device.account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    all_devices = db.query(AccountDevice).filter(AccountDevice.account_id == account.id, AccountDevice.is_active == True).order_by(AccountDevice.last_seen_at.desc()).all()
    return {"status": "success", "plan_code": account.plan_code, "max_devices": _get_plan_max_devices(account.plan_code, db), "used_devices": len(all_devices), "current_device_id": device.device_id, "devices": [{"device_id": d.device_id, "device_name": d.device_name or "Appareil sans nom", "platform": d.platform, "app_version": d.app_version, "is_current": d.device_id == device.device_id, "created_at": d.created_at.isoformat() if d.created_at else None, "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None} for d in all_devices]}

@app.post("/api/account/devices/revoke")
def revoke_account_device(req: RevokeDeviceRequest, db: Session = Depends(get_db)):
    clean_target = req.device_id_to_revoke.strip().upper()
    current_device = db.query(AccountDevice).filter(AccountDevice.session_token == req.session_token).first()
    if not current_device or not current_device.is_active: raise HTTPException(401, "Session invalide.")
    account = db.query(Account).filter(Account.id == current_device.account_id).first()
    if current_device.device_id.strip().upper() == clean_target: raise HTTPException(400, "Impossible de révoquer l'appareil courant.")
    target = db.query(AccountDevice).filter(AccountDevice.account_id == account.id, AccountDevice.device_id == clean_target, AccountDevice.is_active == True).first()
    if not target: raise HTTPException(404, "Appareil introuvable.")
    target.is_active = False
    target.revoked_at = get_utc_now()
    target.revoked_reason = "Révoqué depuis un autre appareil"
    db.commit()
    return {"status": "success", "message": f"Appareil révoqué."}

@app.post("/api/account/upgrade")
def account_upgrade(req: AccountUpgradeRequest, db: Session = Depends(get_db)):
    device = db.query(AccountDevice).filter(AccountDevice.session_token == req.session_token).first()
    if not device or not device.is_active: raise HTTPException(401, "Session invalide.")
    account = db.query(Account).filter(Account.id == device.account_id).first()
    plan = db.query(LicensePlan).filter(LicensePlan.code == req.plan_code).first()
    if not plan: raise HTTPException(404, "Plan introuvable.")
    now = get_utc_now()
    account.plan_code = req.plan_code
    account.is_yearly = req.is_yearly
    account.is_trial = False
    account.purchase_token = req.purchase_token
    account.purchase_platform = req.purchase_platform
    account.expires_at = now + timedelta(days=365 if req.is_yearly else 30)
    db.commit()
    db.refresh(account)
    return {"status": "success", "message": f"Plan {plan.name} activé.", "plan_code": account.plan_code, "is_yearly": account.is_yearly, "expires_at": account.expires_at.isoformat() if account.expires_at else None, "access": _account_to_access_payload(account, db)}

# ═══════════════════════════════════════════════════════════
# ROUTES ADMIN
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# CLÉ DE SAUVEGARDE (app v2)
# ═══════════════════════════════════════════════════════════
class BackupKeyRequest(BaseModel):
    session_token: str
    device_id: str

def _derive_backup_key(account_id: int) -> str:
    """Per-account key: HMAC-SHA256(secret, account id), base64url."""
    msg = f"smartcollect-backup-v{BACKUP_KEY_VERSION}:{account_id}".encode()
    digest = hmac.new(BACKUP_MASTER_SECRET.encode(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")

@app.post("/api/account/backup-key")
def account_backup_key(req: BackupKeyRequest, db: Session = Depends(get_db)):
    """Key that encrypts the account's backups. Only the account owner,
    with a valid session, gets it: the same account can restore its
    backups on a new device; another account cannot read them.
    An expired subscription does not block it: users can always restore
    their own data."""
    if not BACKUP_MASTER_SECRET:
        raise HTTPException(503, "Sauvegarde chiffrée indisponible.")
    clean_device = req.device_id.strip().upper()
    device = db.query(AccountDevice).filter(AccountDevice.session_token == req.session_token, AccountDevice.device_id == clean_device).first()
    if not device: raise HTTPException(401, "Session introuvable.")
    if not device.is_active: raise HTTPException(403, "Session révoquée.")
    if device.session_expires_at and get_utc_now() > device.session_expires_at: raise HTTPException(401, "Session expirée.")
    account = db.query(Account).filter(Account.id == device.account_id).first()
    if not account or not account.is_active: raise HTTPException(403, "Compte désactivé.")
    return {"status": "success", "account_id": account.id, "key_version": BACKUP_KEY_VERSION, "key": _derive_backup_key(account.id)}

# ═══════════════════════════════════════════════════════════
# SUPPRESSION DU COMPTE (exigence Google Play)
# ═══════════════════════════════════════════════════════════
class DeleteAccountRequest(BaseModel):
    session_token: str
    device_id: str
    password: str

class DeleteAccountByPasswordRequest(BaseModel):
    email: str
    password: str

def _delete_account(account: Account, db: Session) -> None:
    """Deletes the account and everything the server keeps about it
    (its devices). Work data never reaches the server."""
    _purge_reset_codes(db, account.email)
    db.query(AccountDevice).filter(AccountDevice.account_id == account.id).delete(synchronize_session=False)
    db.delete(account)
    db.commit()

@app.post("/api/account/delete")
def account_delete(req: DeleteAccountRequest, db: Session = Depends(get_db)):
    """From the app: a valid session and the password again."""
    clean_device = req.device_id.strip().upper()
    device = db.query(AccountDevice).filter(AccountDevice.session_token == req.session_token, AccountDevice.device_id == clean_device).first()
    if not device or not device.is_active: raise HTTPException(401, "Session invalide.")
    account = db.query(Account).filter(Account.id == device.account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    if not _verify_password(req.password, account.password_hash): raise HTTPException(401, "Mot de passe incorrect.")
    _delete_account(account, db)
    return {"status": "success", "message": "Compte supprimé."}

@app.post("/api/account/delete-by-password")
def account_delete_by_password(req: DeleteAccountByPasswordRequest, db: Session = Depends(get_db)):
    """From the web page (/delete-account), without the app."""
    email = _normalize_email(req.email)
    account = db.query(Account).filter(Account.email == email).first()
    # Same answer for an unknown e-mail and a wrong password.
    if not account or not _verify_password(req.password, account.password_hash):
        raise HTTPException(401, "E-mail ou mot de passe incorrect.")
    _delete_account(account, db)
    return {"status": "success", "message": "Compte supprimé."}

@app.get("/delete-account", response_class=HTMLResponse)
def delete_account_page(lang: str = "fr"):
    """Public page to delete an account without the app (Google Play)."""
    return delete_account_page_html(lang)

@app.post("/api/admin/session")
def admin_open_session(req: AdminSessionRequest, request: Request):
    """Exchanges the admin key for a short-lived token (8 h) so the web
    console never has to keep the key itself in the browser."""
    if not ADMIN_API_KEY:
        raise HTTPException(503, "Service admin désactivé.")
    ip = _client_ip(request)
    _admin_check_blocked(ip)
    if not hmac.compare_digest((req.key or "").strip().encode(), ADMIN_API_KEY.encode()):
        _admin_register_failure(ip)
        raise HTTPException(401, "Clé admin invalide.")
    token, exp = _admin_make_token()
    return {"status": "success", "token": token, "expires_at": datetime.fromtimestamp(exp, timezone.utc).isoformat().replace("+00:00", "Z"), "hours": ADMIN_SESSION_HOURS}

@app.get("/api/admin/plans", dependencies=[Depends(require_admin_key)])
def admin_get_plans(db: Session = Depends(get_db)):
    plans = db.query(LicensePlan).order_by(LicensePlan.display_order.asc()).all()
    return [{"id": p.id, "code": p.code, "name": p.name, "description": p.description or "", "price_monthly_usd": float(p.price_monthly_usd or 0), "price_yearly_usd": float(p.price_yearly_usd or 0), "max_tables": p.max_tables, "max_rows_per_table": p.max_rows_per_table, "allow_export": bool(p.allow_export), "allow_cloud_backup": bool(p.allow_cloud_backup), "allow_bulk_export": bool(p.allow_bulk_export), "allow_merge_tables": bool(p.allow_merge_tables), "allow_custom_branding": bool(p.allow_custom_branding), "allow_reminders": bool(p.allow_reminders), "max_devices": p.max_devices, "is_active": bool(p.is_active), "display_order": p.display_order} for p in plans]

@app.put("/api/admin/plans/{code}", dependencies=[Depends(require_admin_key)])
def admin_update_plan(code: str, req: UpdatePlanRequest, request: Request, db: Session = Depends(get_db)):
    plan = db.query(LicensePlan).filter(LicensePlan.code == code).first()
    if not plan: raise HTTPException(404, "Plan introuvable.")
    changes = {}
    for field, value in req.dict(exclude_unset=True).items():
        if value is None: continue
        if field in ("price_monthly_usd", "price_yearly_usd") and value < 0: raise HTTPException(400, "Un prix ne peut pas être négatif.")
        if field in ("max_tables", "max_rows_per_table", "max_devices") and value < 1: raise HTTPException(400, "Les limites doivent être d'au moins 1.")
        old = getattr(plan, field)
        if old != value:
            changes[field] = [old, value]
            setattr(plan, field, value)
    db.commit()
    if changes:
        affected = db.query(Account).filter(Account.plan_code == plan.code).count()
        _audit(db, request, "plan.update", plan.id, plan.code, {"changes": changes, "accounts_affected": affected}, target_type="plan")
    return {"status": "success", "plan": {"code": plan.code, "name": plan.name}}

# ═══════════════════════════════════════════════════════════
# ENTREPRISES : un responsable, des postes, des employés
# ═══════════════════════════════════════════════════════════
def _session_account(session_token: str, db: Session) -> Account:
    device = db.query(AccountDevice).filter(AccountDevice.session_token == session_token).first()
    if not device or not device.is_active: raise HTTPException(401, "Session invalide.")
    account = db.query(Account).filter(Account.id == device.account_id).first()
    if not account or not account.is_active: raise HTTPException(403, "Compte désactivé.")
    return account

def _org_owner(session_token: str, db: Session):
    account = _session_account(session_token, db)
    org = _org_of(account, db)
    if not org: raise HTTPException(404, "Ce compte n'appartient à aucune entreprise.")
    if account.org_role != "owner": raise HTTPException(403, "Réservé au responsable de l'entreprise.")
    return account, org

def _member_dict(m: Account, now) -> dict:
    return {"id": m.id, "email": m.email, "first_name": m.first_name or "", "last_name": m.last_name or "", "role": m.org_role or "member", "is_active": bool(m.is_active), "last_login_at": _iso(m.last_login_at)}

class OrgSessionRequest(BaseModel):
    session_token: str

class OrgAddMemberRequest(BaseModel):
    session_token: str
    email: str
    password: str = Field(..., min_length=8, max_length=100)
    first_name: str = ""
    last_name: str = ""

class OrgMemberRequest(BaseModel):
    session_token: str
    account_id: int

class OrgMemberPasswordRequest(BaseModel):
    session_token: str
    account_id: int
    new_password: str = Field(..., min_length=8, max_length=100)

@app.post("/api/org/info")
def org_info(req: OrgSessionRequest, db: Session = Depends(get_db)):
    """My company: name, seats, and (for the owner) the employees."""
    account = _session_account(req.session_token, db)
    org = _org_of(account, db)
    if not org: return {"status": "success", "organization": None}
    out = {"status": "success", "organization": _org_payload(org, account, db)}
    if account.org_role == "owner":
        now = get_utc_now()
        out["members"] = [_member_dict(m, now) for m in db.query(Account).filter(Account.organization_id == org.id).order_by(Account.id.asc()).all()]
    return out

@app.post("/api/org/members/add")
def org_add_member(req: OrgAddMemberRequest, request: Request, db: Session = Depends(get_db)):
    owner, org = _org_owner(req.session_token, db)
    if not org.is_active: raise HTTPException(403, "L'entreprise est désactivée.")
    email = _normalize_email(req.email)
    if not _validate_email(email): raise HTTPException(400, "Adresse e-mail invalide.")
    used = _org_seats_used(org, db)
    if used >= org.seats:
        _audit(db, request, "org.seats_full", org.id, org.name, {"seats": org.seats, "tried": _mask_email(email)}, target_type="org")
        raise HTTPException(409, f"Nombre de postes atteint ({used} sur {org.seats}). Contactez-nous pour en ajouter.")
    existing = db.query(Account).filter(Account.email == email).first()
    if existing:
        # A person removed from a company earlier can be taken back; any
        # other existing account is its own (never taken over).
        if existing.organization_id is not None or existing.is_active or existing.grant_source != "org":
            raise HTTPException(409, "Cette adresse a déjà un compte.")
        existing.password_hash = _hash_password(req.password)
        member = existing
        member.is_active = True
    else:
        member = Account(email=email, password_hash=_hash_password(req.password), created_at=get_utc_now())
        db.add(member)
    member.first_name = req.first_name.strip()[:100]
    member.last_name = req.last_name.strip()[:100]
    member.organization_id = org.id
    member.org_role = "member"
    _sync_org_members(org, db)
    db.commit()
    _audit(db, request, "org.member_add", org.id, org.name, {"member": _mask_email(email)}, target_type="org")
    used = _org_seats_used(org, db)
    return {"status": "success", "member": _member_dict(member, get_utc_now()), "seats": org.seats, "used": used, "full": used >= org.seats}

@app.post("/api/org/members/remove")
def org_remove_member(req: OrgMemberRequest, request: Request, db: Session = Depends(get_db)):
    owner, org = _org_owner(req.session_token, db)
    member = db.query(Account).filter(Account.id == req.account_id, Account.organization_id == org.id).first()
    if not member: raise HTTPException(404, "Employé introuvable.")
    if member.id == owner.id: raise HTTPException(400, "Le responsable ne peut pas se retirer lui-même.")
    # The seat is freed; the account and its cloud backups are kept.
    member.organization_id = None
    member.org_role = None
    member.is_active = False
    db.query(AccountDevice).filter(AccountDevice.account_id == member.id).update({"is_active": False})
    db.commit()
    _audit(db, request, "org.member_remove", org.id, org.name, {"member": _mask_email(member.email)}, target_type="org")
    used = _org_seats_used(org, db)
    return {"status": "success", "seats": org.seats, "used": used, "full": used >= org.seats}

@app.post("/api/org/members/password")
def org_member_password(req: OrgMemberPasswordRequest, request: Request, db: Session = Depends(get_db)):
    owner, org = _org_owner(req.session_token, db)
    member = db.query(Account).filter(Account.id == req.account_id, Account.organization_id == org.id).first()
    if not member: raise HTTPException(404, "Employé introuvable.")
    member.password_hash = _hash_password(req.new_password)
    db.query(AccountDevice).filter(AccountDevice.account_id == member.id).update({"is_active": False})
    db.commit()
    _audit(db, request, "org.member_password", org.id, org.name, {"member": _mask_email(member.email)}, target_type="org")
    return {"status": "success"}

# ─── Admin : entreprises ───
class AdminOrgRequest(BaseModel):
    name: Optional[str] = None
    plan_code: Optional[str] = None
    seats: Optional[int] = None
    expires_at: Optional[str] = None  # ISO date; "" = no end
    is_active: Optional[bool] = None
    admin_note: Optional[str] = None

class AdminOrgOwnerRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=8, max_length=100)
    first_name: str = ""
    last_name: str = ""

def _org_admin_dict(o: Organization, db: Session) -> dict:
    return {"id": o.id, "name": o.name, "plan_code": o.plan_code, "seats": o.seats, "used": _org_seats_used(o, db), "expires_at": _iso(o.expires_at), "is_active": bool(o.is_active), "admin_note": o.admin_note or "", "created_at": _iso(o.created_at)}

def _parse_org_date(s: str):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)
    except ValueError: raise HTTPException(400, "Date invalide.")

@app.get("/api/admin/orgs", dependencies=[Depends(require_admin_key)])
def admin_orgs(db: Session = Depends(get_db)):
    return [_org_admin_dict(o, db) for o in db.query(Organization).order_by(Organization.id.desc()).all()]

@app.get("/api/admin/orgs/{org_id}", dependencies=[Depends(require_admin_key)])
def admin_org(org_id: int, db: Session = Depends(get_db)):
    o = db.query(Organization).filter(Organization.id == org_id).first()
    if not o: raise HTTPException(404, "Entreprise introuvable.")
    now = get_utc_now()
    return {**_org_admin_dict(o, db), "members": [_member_dict(m, now) for m in db.query(Account).filter(Account.organization_id == o.id).order_by(Account.id.asc()).all()]}

@app.post("/api/admin/orgs", dependencies=[Depends(require_admin_key)])
def admin_create_org(req: AdminOrgRequest, request: Request, db: Session = Depends(get_db)):
    name = (req.name or "").strip()
    if not name: raise HTTPException(400, "Le nom est obligatoire.")
    plan = req.plan_code or "enterprise"
    if not db.query(LicensePlan).filter(LicensePlan.code == plan).first(): raise HTTPException(400, "Plan inconnu.")
    o = Organization(name=name[:150], plan_code=plan, seats=max(1, req.seats or 5), expires_at=_parse_org_date(req.expires_at or ""), admin_note=req.admin_note or "")
    db.add(o)
    db.commit()
    _audit(db, request, "org.create", o.id, o.name, {"seats": o.seats, "plan": o.plan_code}, target_type="org")
    return _org_admin_dict(o, db)

@app.put("/api/admin/orgs/{org_id}", dependencies=[Depends(require_admin_key)])
def admin_update_org(org_id: int, req: AdminOrgRequest, request: Request, db: Session = Depends(get_db)):
    o = db.query(Organization).filter(Organization.id == org_id).first()
    if not o: raise HTTPException(404, "Entreprise introuvable.")
    changes = {}
    def setf(name, value):
        old = getattr(o, name)
        if old != value:
            changes[name] = [str(old), str(value)]
            setattr(o, name, value)
    if req.name is not None:
        if not req.name.strip(): raise HTTPException(400, "Le nom est obligatoire.")
        setf("name", req.name.strip()[:150])
    if req.plan_code is not None:
        if not db.query(LicensePlan).filter(LicensePlan.code == req.plan_code).first(): raise HTTPException(400, "Plan inconnu.")
        setf("plan_code", req.plan_code)
    if req.seats is not None:
        used = _org_seats_used(o, db)
        if req.seats < max(1, used): raise HTTPException(400, f"{used} postes sont déjà utilisés : retirez des employés avant de réduire.")
        setf("seats", req.seats)
    if req.expires_at is not None: setf("expires_at", _parse_org_date(req.expires_at))
    if req.is_active is not None: setf("is_active", req.is_active)
    if req.admin_note is not None: setf("admin_note", req.admin_note)
    if req.is_active is True:
        for m in db.query(Account).filter(Account.organization_id == o.id).all(): m.is_active = True
    _sync_org_members(o, db)
    db.commit()
    if changes: _audit(db, request, "org.update", o.id, o.name, {"changes": changes}, target_type="org")
    return _org_admin_dict(o, db)

@app.post("/api/admin/orgs/{org_id}/owner", dependencies=[Depends(require_admin_key)])
def admin_org_owner(org_id: int, req: AdminOrgOwnerRequest, request: Request, db: Session = Depends(get_db)):
    """Creates the company's owner account (the one who adds the employees)."""
    o = db.query(Organization).filter(Organization.id == org_id).first()
    if not o: raise HTTPException(404, "Entreprise introuvable.")
    email = _normalize_email(req.email)
    if not _validate_email(email): raise HTTPException(400, "Adresse e-mail invalide.")
    if db.query(Account).filter(Account.email == email).first(): raise HTTPException(409, "Cette adresse a déjà un compte.")
    if _org_seats_used(o, db) >= o.seats: raise HTTPException(409, "Aucun poste libre.")
    owner = Account(email=email, password_hash=_hash_password(req.password), first_name=req.first_name.strip()[:100], last_name=req.last_name.strip()[:100], organization_id=o.id, org_role="owner", created_at=get_utc_now())
    db.add(owner)
    _sync_org_members(o, db)
    db.flush()
    _sync_org_members(o, db)
    db.commit()
    _audit(db, request, "org.owner_create", o.id, o.name, {"owner": _mask_email(email)}, target_type="org")
    return {"status": "success", "owner": _member_dict(owner, get_utc_now())}

@app.get("/api/admin/play/status", dependencies=[Depends(require_admin_key)])
def admin_play_status():
    """Whether prices can be sent to Google Play from here."""
    return {"configured": play_billing.configured(), "package": play_billing.package_name(), "product_prefix": play_billing.product_prefix()}

class SyncPlayRequest(BaseModel):
    dry_run: bool = False

@app.post("/api/admin/plans/{code}/sync-play", dependencies=[Depends(require_admin_key)])
def admin_sync_play(code: str, request: Request, req: SyncPlayRequest = SyncPlayRequest(), db: Session = Depends(get_db)):
    """Sends this plan's monthly and yearly USD prices to Google Play (new
    subscribers). dry_run only checks what would change."""
    plan = db.query(LicensePlan).filter(LicensePlan.code == code).first()
    if not plan: raise HTTPException(404, "Plan introuvable.")
    prices = {"monthly": float(plan.price_monthly_usd or 0), "yearly": float(plan.price_yearly_usd or 0)}
    try:
        result = play_billing.sync_plan(plan.code, prices, dry_run=req.dry_run)
    except play_billing.PlayError as e:
        _audit(db, request, "plan.sync_play_failed", plan.id, plan.code, {"error": str(e), "prices": prices}, target_type="plan")
        raise HTTPException(502, str(e))
    _audit(db, request, "plan.sync_play_check" if req.dry_run else "plan.sync_play", plan.id, plan.code, {"prices": prices, **result}, target_type="plan")
    return {"status": "success", "prices_usd": prices, **result}

# ─── Comptes : lecture ───
@app.get("/api/admin/accounts", dependencies=[Depends(require_admin_key)])
def admin_get_accounts(db: Session = Depends(get_db)):
    """Legacy full list (kept for the desktop tool). The web console uses
    /api/admin/accounts/search (paginated)."""
    accounts = db.query(Account).order_by(Account.id.desc()).all()
    counts = _device_counts(db, [a.id for a in accounts])
    results = []
    for acc in accounts:
        results.append({"id": acc.id, "email": acc.email, "first_name": acc.first_name or "", "last_name": acc.last_name or "", "phone_number": acc.phone_number or "", "plan_code": acc.plan_code, "is_yearly": bool(acc.is_yearly), "is_trial": bool(acc.is_trial), "is_active": bool(acc.is_active), "expires_at": acc.expires_at.isoformat() if acc.expires_at else None, "purchase_token": acc.purchase_token, "active_devices": counts.get(acc.id, 0), "created_at": acc.created_at.isoformat() if acc.created_at else "", "last_login_at": acc.last_login_at.isoformat() if acc.last_login_at else None})
    return results

@app.get("/api/admin/accounts/search", dependencies=[Depends(require_admin_key)])
def admin_search_accounts(q: str = "", status: str = "all", plan: str = "", sort: str = "created_at", direction: str = "desc", page: int = 1, page_size: int = 25, db: Session = Depends(get_db)):
    now = get_utc_now()
    page = max(1, page)
    page_size = min(100, max(5, page_size))
    query = _filtered_accounts(db, q, status, plan, now)
    total = query.count()
    sort_cols = {"created_at": Account.created_at, "last_login_at": Account.last_login_at, "expires_at": Account.expires_at, "email": Account.email, "id": Account.id}
    col = sort_cols.get(sort, Account.created_at)
    order = col.asc() if direction == "asc" else col.desc()
    rows = query.order_by(order, Account.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    counts = _device_counts(db, [a.id for a in rows])
    return {"total": total, "page": page, "page_size": page_size, "pages": max(1, -(-total // page_size)), "items": [_account_dict(a, now, counts.get(a.id, 0)) for a in rows]}

@app.get("/api/admin/stats", dependencies=[Depends(require_admin_key)])
def admin_stats(db: Session = Depends(get_db)):
    now = get_utc_now()
    accounts = db.query(Account).all()
    plans = {p.code: p for p in db.query(LicensePlan).all()}
    total = len(accounts)
    by_status = {"trial": 0, "paid": 0, "expired": 0, "disabled": 0}
    by_plan = {}
    expiring_trials, expiring_paid, admin_grants = [], [], 0
    signups = {}
    mrr = 0.0
    paying_play = 0
    for a in accounts:
        st = _account_status(a, now)
        by_status[st] += 1
        by_plan[a.plan_code or "trial"] = by_plan.get(a.plan_code or "trial", 0) + 1
        if a.created_at and (now - a.created_at).days < 30:
            key = a.created_at.date().isoformat()
            signups[key] = signups.get(key, 0) + 1
        if st in ("trial", "paid") and a.expires_at and a.expires_at - now <= timedelta(days=7):
            (expiring_trials if st == "trial" else expiring_paid).append(_account_dict(a, now))
        if st == "paid":
            src = _account_source(a)
            if src == "admin": admin_grants += 1
            else:
                paying_play += 1
                p = plans.get(a.plan_code)
                if p:
                    mrr += (float(p.price_yearly_usd or 0) / 12.0) if a.is_yearly else float(p.price_monthly_usd or 0)
    days = [(now.date() - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    devices_active = db.query(AccountDevice).filter(AccountDevice.is_active == True).count()
    last7 = sum(v for k, v in signups.items() if k >= (now.date() - timedelta(days=6)).isoformat())
    expiring_trials.sort(key=lambda x: x["expires_at"] or "")
    expiring_paid.sort(key=lambda x: x["expires_at"] or "")
    return {"generated_at": _iso(now), "total": total, "by_status": by_status, "by_plan": by_plan, "signups_7d": last7, "signups_30d": sum(signups.values()), "signups_by_day": [{"day": d, "count": signups.get(d, 0)} for d in days], "devices_active": devices_active, "expiring_trials": expiring_trials[:10], "expiring_paid": expiring_paid[:10], "admin_grants": admin_grants, "paying_store": paying_play, "mrr_estimate_usd": round(mrr, 2), "arr_estimate_usd": round(mrr * 12, 2), "plans": {c: {"name": p.name, "price_monthly_usd": float(p.price_monthly_usd or 0), "price_yearly_usd": float(p.price_yearly_usd or 0), "is_active": bool(p.is_active)} for c, p in plans.items()}}

@app.get("/api/admin/accounts/export.csv", dependencies=[Depends(require_admin_key)])
def admin_export_accounts(request: Request, q: str = "", status: str = "all", plan: str = "", db: Session = Depends(get_db)):
    now = get_utc_now()
    rows = _filtered_accounts(db, q, status, plan, now).order_by(Account.id.asc()).all()
    counts = _device_counts(db, [a.id for a in rows])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "email", "prenom", "nom", "telephone", "plan", "statut", "source", "annuel", "expire_le_utc", "inscrit_le_utc", "derniere_connexion_utc", "appareils_actifs", "note"])
    for a in rows:
        d = _account_dict(a, now, counts.get(a.id, 0))
        w.writerow([_csv_safe(x) for x in [a.id, a.email, a.first_name or "", a.last_name or "", a.phone_number or "", a.plan_code, d["status"], d["source"], "oui" if a.is_yearly else "non", d["expires_at"] or "", d["created_at"] or "", d["last_login_at"] or "", d["active_devices"], a.admin_note or ""]])
    _audit(db, request, "accounts.export", None, f"{len(rows)} comptes", {"q": q, "status": status, "plan": plan}, target_type="export")
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="tablyo-comptes.csv"', "Cache-Control": "no-store"})

@app.get("/api/admin/accounts/{account_id}", dependencies=[Depends(require_admin_key)])
def admin_account_detail(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    now = get_utc_now()
    devices = db.query(AccountDevice).filter(AccountDevice.account_id == account_id).order_by(AccountDevice.last_seen_at.desc()).all()
    audit = db.query(AdminAudit).filter(AdminAudit.target_type == "account", AdminAudit.target_id == account_id).order_by(AdminAudit.id.desc()).limit(50).all()
    data = _account_dict(account, now, sum(1 for d in devices if d.is_active))
    data["max_devices"] = _get_plan_max_devices(account.plan_code, db)
    data["devices"] = [{"id": d.id, "device_id": d.device_id, "device_name": d.device_name or "", "platform": d.platform, "app_version": d.app_version, "is_active": bool(d.is_active), "created_at": _iso(d.created_at), "last_seen_at": _iso(d.last_seen_at), "revoked_at": _iso(d.revoked_at), "revoked_reason": d.revoked_reason or ""} for d in devices]
    data["audit"] = [_audit_dict(a) for a in audit]
    return data

@app.get("/api/admin/accounts/{account_id}/devices", dependencies=[Depends(require_admin_key)])
def admin_get_account_devices(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    devices = db.query(AccountDevice).filter(AccountDevice.account_id == account_id).order_by(AccountDevice.last_seen_at.desc()).all()
    return {"status": "success", "account_email": account.email, "plan_code": account.plan_code, "devices": [{"id": d.id, "device_id": d.device_id, "device_name": d.device_name or "", "platform": d.platform, "app_version": d.app_version, "is_active": bool(d.is_active), "created_at": d.created_at.isoformat() if d.created_at else None, "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None, "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None, "revoked_reason": d.revoked_reason or ""} for d in devices]}

# ─── Comptes : actions ───
@app.post("/api/admin/accounts/{account_id}/revoke-device", dependencies=[Depends(require_admin_key)])
def admin_revoke_account_device(account_id: int, device_id: str, request: Request, db: Session = Depends(get_db)):
    device = db.query(AccountDevice).filter(AccountDevice.account_id == account_id, AccountDevice.device_id == device_id.strip().upper()).first()
    if not device: raise HTTPException(404, "Appareil introuvable.")
    device.is_active = False
    device.revoked_at = get_utc_now()
    device.revoked_reason = "Révoqué par admin"
    db.commit()
    _audit(db, request, "device.revoke", account_id, _label(db, account_id), {"device": device.device_name or device.device_id[:12]})
    return {"status": "success", "message": "Appareil révoqué."}

@app.post("/api/admin/accounts/{account_id}/toggle-status", dependencies=[Depends(require_admin_key)])
def admin_toggle_account_status(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    account.is_active = not account.is_active
    db.commit()
    _audit(db, request, "account.enable" if account.is_active else "account.disable", account_id, _mask_email(account.email))
    return {"status": "success", "is_active": account.is_active}

@app.put("/api/admin/accounts/{account_id}", dependencies=[Depends(require_admin_key)])
def admin_update_account(account_id: int, req: AdminUpdateAccountRequest, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    changes = {}
    def setf(name, value):
        old = getattr(account, name)
        if old != value:
            changes[name] = [old, value]
            setattr(account, name, value)
    if req.first_name is not None: setf("first_name", req.first_name.strip()[:100])
    if req.last_name is not None: setf("last_name", req.last_name.strip()[:100])
    if req.phone_number is not None: setf("phone_number", req.phone_number.strip()[:50])
    if req.is_active is not None: setf("is_active", req.is_active)
    if req.plan_code is not None:
        if not db.query(LicensePlan).filter(LicensePlan.code == req.plan_code).first(): raise HTTPException(400, "Plan inconnu.")
        setf("plan_code", req.plan_code)
    if req.is_yearly is not None: setf("is_yearly", req.is_yearly)
    if req.is_trial is not None: setf("is_trial", req.is_trial)
    db.commit()
    if changes: _audit(db, request, "account.update", account_id, _mask_email(account.email), {"changes": changes})
    return {"status": "success", "message": "Compte mis à jour."}

@app.put("/api/admin/accounts/{account_id}/note", dependencies=[Depends(require_admin_key)])
def admin_account_note(account_id: int, req: AdminNoteRequest, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    account.admin_note = (req.note or "").strip()[:2000]
    db.commit()
    _audit(db, request, "account.note", account_id, _mask_email(account.email))
    return {"status": "success"}

@app.post("/api/admin/accounts/{account_id}/grant-full-access", dependencies=[Depends(require_admin_key)])
def admin_grant_full_access_account(account_id: int, req: AdminGrantFullAccessRequest, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    plan = db.query(LicensePlan).filter(LicensePlan.code == req.plan_code).first()
    if not plan: raise HTTPException(404, "Plan introuvable.")
    if req.duration_val < 1 or req.duration_val > 100000: raise HTTPException(400, "Durée invalide.")
    new_days_float = _duration_to_days_float(req.duration_val, req.duration_unit)
    before = {"plan": account.plan_code, "trial": bool(account.is_trial), "expires_at": _iso(account.expires_at)}
    if req.duration_mode == "add" and account.expires_at:
        total_days = max(0, (account.expires_at - get_utc_now()).total_seconds() / 86400.0) + new_days_float
    else: total_days = new_days_float
    account.plan_code = req.plan_code
    account.is_yearly = req.is_yearly
    account.is_trial = False
    account.is_active = True
    if not account.purchase_token: account.grant_source = "admin"
    if req.reset_expiry: account.expires_at = get_utc_now() + timedelta(days=total_days)
    db.commit()
    _audit(db, request, "account.grant", account_id, _mask_email(account.email), {"before": before, "after": {"plan": account.plan_code, "trial": False, "expires_at": _iso(account.expires_at)}, "duration": f"{req.duration_val} {req.duration_unit}", "mode": req.duration_mode})
    return {"status": "success", "message": f"Accès {req.plan_code} accordé.", "expires_at": account.expires_at.isoformat() if account.expires_at else None}

@app.post("/api/admin/accounts/{account_id}/extend", dependencies=[Depends(require_admin_key)])
def admin_extend_account(account_id: int, req: AdminExtendRequest, request: Request, db: Session = Depends(get_db)):
    """Adds time to the current end date (or to now if already expired)
    without touching the plan."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    if req.days <= 0 or req.days > 3650: raise HTTPException(400, "Durée invalide (1 à 3650 jours).")
    now = get_utc_now()
    base = account.expires_at if account.expires_at and account.expires_at > now else now
    before = account.expires_at
    account.expires_at = base + timedelta(days=req.days)
    db.commit()
    _audit(db, request, "account.extend", account_id, _mask_email(account.email), {"days": req.days, "before": _iso(before), "after": _iso(account.expires_at)})
    return {"status": "success", "expires_at": _iso(account.expires_at)}

@app.post("/api/admin/accounts/{account_id}/reset-to-trial", dependencies=[Depends(require_admin_key)])
def admin_reset_account_to_trial(account_id: int, req: AdminResetToTrialRequest, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    if req.duration_val < 1 or req.duration_val > 100000: raise HTTPException(400, "Durée invalide.")
    new_days_float = _duration_to_days_float(req.duration_val, req.duration_unit)
    before = {"plan": account.plan_code, "trial": bool(account.is_trial), "expires_at": _iso(account.expires_at), "had_purchase": bool(account.purchase_token)}
    if req.duration_mode == "add" and account.expires_at:
        total_days = max(0, (account.expires_at - get_utc_now()).total_seconds() / 86400.0) + new_days_float
    else: total_days = new_days_float
    account.plan_code = "trial"
    account.is_trial = True
    account.is_yearly = False
    account.is_active = True
    account.grant_source = "trial"
    account.expires_at = get_utc_now() + timedelta(days=total_days)
    db.commit()
    _audit(db, request, "account.reset_trial", account_id, _mask_email(account.email), {"before": before, "after_expires_at": _iso(account.expires_at)})
    return {"status": "success", "message": "Compte remis en essai."}

@app.post("/api/admin/accounts/{account_id}/reset-devices", dependencies=[Depends(require_admin_key)])
def admin_reset_account_devices(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    active_devices = db.query(AccountDevice).filter(AccountDevice.account_id == account.id, AccountDevice.is_active == True).all()
    for d in active_devices:
        d.is_active = False
        d.revoked_at = get_utc_now()
        d.revoked_reason = "Reset admin"
    db.commit()
    _audit(db, request, "devices.reset", account_id, _mask_email(account.email), {"count": len(active_devices)})
    return {"status": "success", "count": len(active_devices)}

@app.post("/api/admin/accounts/bulk", dependencies=[Depends(require_admin_key)])
def admin_bulk_accounts(req: AdminBulkRequest, request: Request, db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(req.ids))[:200]
    if not ids: raise HTTPException(400, "Aucun compte sélectionné.")
    if req.action not in ("activate", "deactivate", "reset_devices", "extend"): raise HTTPException(400, "Action inconnue.")
    if req.action == "extend" and (not req.days or req.days <= 0 or req.days > 3650): raise HTTPException(400, "Durée invalide (1 à 3650 jours).")
    accounts = db.query(Account).filter(Account.id.in_(ids)).all()
    now = get_utc_now()
    done = 0
    for a in accounts:
        if req.action == "activate": a.is_active = True
        elif req.action == "deactivate": a.is_active = False
        elif req.action == "extend":
            base = a.expires_at if a.expires_at and a.expires_at > now else now
            a.expires_at = base + timedelta(days=req.days)
        elif req.action == "reset_devices":
            for d in db.query(AccountDevice).filter(AccountDevice.account_id == a.id, AccountDevice.is_active == True).all():
                d.is_active = False
                d.revoked_at = now
                d.revoked_reason = "Reset admin (lot)"
        done += 1
    db.commit()
    _audit(db, request, f"bulk.{req.action}", None, f"{done} comptes", {"ids": [a.id for a in accounts], "days": req.days}, target_type="bulk")
    return {"status": "success", "count": done}

@app.delete("/api/admin/accounts/{account_id}", dependencies=[Depends(require_admin_key)])
def admin_delete_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account: raise HTTPException(404, "Compte introuvable.")
    label = _mask_email(account.email)
    had = {"plan": account.plan_code, "had_purchase": bool(account.purchase_token), "expires_at": _iso(account.expires_at)}
    _purge_reset_codes(db, account.email)
    db.query(AccountDevice).filter(AccountDevice.account_id == account.id).delete(synchronize_session=False)
    db.delete(account)
    db.commit()
    _audit(db, request, "account.delete", account_id, label, had)
    return {"status": "success", "message": "Compte supprimé."}

# ─── Configuration & journal ───
@app.get("/api/admin/config/default-trial", dependencies=[Depends(require_admin_key)])
def get_default_trial_config(db: Session = Depends(get_db)):
    val, unit, days = _get_default_trial_config(db)
    return {"duration_val": val, "duration_unit": unit, "duration_days": days}

@app.put("/api/admin/config/default-trial", dependencies=[Depends(require_admin_key)])
def update_default_trial_config(req: DefaultTrialConfigRequest, request: Request, db: Session = Depends(get_db)):
    if req.duration_val < 1 or req.duration_val > 100000: raise HTTPException(400, "Durée invalide.")
    if req.duration_unit not in ("Minutes", "Heures", "Jours", "Mois", "Ans"): raise HTTPException(400, "Unité invalide.")
    old = _get_default_trial_config(db)
    _set_default_trial_config(db, req.duration_val, req.duration_unit)
    _audit(db, request, "config.default_trial", None, "Essai par défaut", {"before": f"{old[0]} {old[1]}", "after": f"{req.duration_val} {req.duration_unit}"}, target_type="config")
    return {"status": "success", "message": "Config mise à jour."}

@app.get("/api/admin/audit", dependencies=[Depends(require_admin_key)])
def admin_audit_list(action: str = "", account_id: int = 0, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    limit = min(200, max(1, limit))
    q = db.query(AdminAudit)
    if action: q = q.filter(AdminAudit.action.like(action.replace("%", "") + "%"))
    if account_id: q = q.filter(AdminAudit.target_type == "account", AdminAudit.target_id == account_id)
    total = q.count()
    rows = q.order_by(AdminAudit.id.desc()).offset(max(0, offset)).limit(limit).all()
    return {"total": total, "items": [_audit_dict(a) for a in rows]}

# ═══════════════════════════════════════════════════════════
# ACTUALITÉS
# ═══════════════════════════════════════════════════════════
@app.get("/api/news")
def get_all_news(db: Session = Depends(get_db)):
    news_items = db.query(AppNews).order_by(AppNews.id.desc()).all()
    return [{"id": str(n.id), "title": str(n.title or ""), "summary": str(n.summary or ""), "content": str(n.content or ""), "category": str(n.category or "news"), "version": n.version, "download_url": n.download_url, "created_at": n.created_at.isoformat() if n.created_at else get_utc_now().isoformat()} for n in news_items]

_NEWS_CATEGORIES = ("news", "update", "tip", "alert")

def _clean_news_fields(title: str, summary: str, category: str, url: Optional[str]):
    title, summary = (title or "").strip(), (summary or "").strip()
    if not title or not summary: raise HTTPException(400, "Titre et résumé obligatoires.")
    cat = category if category in _NEWS_CATEGORIES else "news"
    url = (url or "").strip() or None
    if url and not url.lower().startswith("https://"): raise HTTPException(400, "Le lien de téléchargement doit commencer par https://")
    return title[:255], summary, cat, url

@app.post("/api/admin/news/create", dependencies=[Depends(require_admin_key)])
def create_admin_news(req: NewsCreateRequest, request: Request, db: Session = Depends(get_db)):
    title, summary, cat, url = _clean_news_fields(req.title, req.summary, req.category, req.download_url)
    new_article = AppNews(title=title, summary=summary, content=req.content.strip() if req.content else "", category=cat, version=req.version.strip() if req.version else None, download_url=url, created_at=get_utc_now())
    db.add(new_article)
    db.commit()
    db.refresh(new_article)
    _audit(db, request, "news.create", new_article.id, title[:80], target_type="news")
    return {"status": "success", "id": new_article.id}

@app.put("/api/admin/news/{news_id}", dependencies=[Depends(require_admin_key)])
def update_admin_news(news_id: int, req: NewsCreateRequest, request: Request, db: Session = Depends(get_db)):
    item = db.query(AppNews).filter(AppNews.id == news_id).first()
    if not item: raise HTTPException(404, "Article introuvable.")
    title, summary, cat, url = _clean_news_fields(req.title, req.summary, req.category, req.download_url)
    item.title, item.summary, item.category, item.download_url = title, summary, cat, url
    item.content = req.content.strip() if req.content else ""
    item.version = req.version.strip() if req.version else None
    db.commit()
    _audit(db, request, "news.update", news_id, title[:80], target_type="news")
    return {"status": "success"}

@app.delete("/api/admin/news/{news_id}", dependencies=[Depends(require_admin_key)])
def delete_admin_news(news_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(AppNews).filter(AppNews.id == news_id).first()
    if not item: raise HTTPException(404, "Article introuvable.")
    title = item.title or ""
    db.delete(item)
    db.commit()
    _audit(db, request, "news.delete", news_id, title[:80], target_type="news")
    return {"status": "success", "message": "Supprimé."}

# ═══════════════════════════════════════════════════════════
# CONSOLE WEB D'ADMINISTRATION (/admin)
# ═══════════════════════════════════════════════════════════
_ADMIN_HTML_FILE = Path(__file__).parent / "admin.html"

@app.get("/admin", response_class=HTMLResponse)
def admin_console():
    """The console itself is a static page: it holds no secret and no data.
    Everything it shows comes from the /api/admin endpoints, behind the key."""
    try:
        html = _ADMIN_HTML_FILE.read_text(encoding="utf-8")
    except Exception:
        raise HTTPException(404, "Console introuvable.")
    nonce = secrets.token_urlsafe(16)
    csp = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    return HTMLResponse(html.replace("__NONCE__", nonce), headers={"Content-Security-Policy": csp, "Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
