import os
import json
import math
import secrets
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ═══════════════════════════════════════════════════════════
# CONFIGURATION GLOBALE
# ═══════════════════════════════════════════════════════════
DEFAULT_TRIAL_DAYS = 7
DEFAULT_TRIAL_UNIT = "Jours"

TRIAL_MAX_TABLES = 1
TRIAL_MAX_ROWS_PER_TABLE = 50
TRIAL_ALLOW_EXPORT = False

DEFAULT_INITIAL_QUOTA = 1

# ═══════════════════════════════════════════════════════════
# 🔐 CLÉ ADMIN
# ═══════════════════════════════════════════════════════════
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
class LicenseKey(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(32), unique=True, index=True, nullable=False)
    phone_number = Column(String(50), default="", nullable=False, index=True)
    first_name = Column(String(100), default="", nullable=True)
    last_name = Column(String(100), default="", nullable=True)
    organization = Column(String(150), default="", nullable=True)
    is_active = Column(Boolean, default=True)
    is_trial = Column(Boolean, default=True)
    device_uuid = Column(Text, default="[]")
    max_devices = Column(Integer, default=1)
    duration_days = Column(Integer, default=DEFAULT_TRIAL_DAYS)
    duration_val = Column(Integer, default=DEFAULT_TRIAL_DAYS)
    duration_unit = Column(String(20), default=DEFAULT_TRIAL_UNIT)
    created_at = Column(DateTime, default=get_utc_now)
    activated_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)


class DeviceAttempt(Base):
    __tablename__ = "device_attempts"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(255), unique=True, index=True, nullable=False)
    phone_number = Column(String(50), nullable=False)
    first_attempt_at = Column(DateTime, default=get_utc_now)
    last_attempt_at = Column(DateTime, default=get_utc_now)
    attempts_count = Column(Integer, default=1)
    max_attempts_allowed = Column(Integer, default=1)
    is_blocked = Column(Boolean, default=False)
    block_reason = Column(String(255), default="")
    notes = Column(Text, default="")
    admin_unblocked = Column(Boolean, default=False)
    admin_unblocked_at = Column(DateTime, nullable=True)


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


# ═══════════════════════════════════════════════════════════
# MIGRATIONS
# ═══════════════════════════════════════════════════════════
print("[startup] Début des migrations...")

migrations = [
    ("DROP email NOT NULL",
     "DO $$ BEGIN "
     "IF EXISTS (SELECT 1 FROM information_schema.columns "
     "WHERE table_name='licenses' AND column_name='email') "
     "THEN ALTER TABLE licenses ALTER COLUMN email DROP NOT NULL; "
     "END IF; END $$;"),
    ("ADD duration_val", "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS duration_val INTEGER DEFAULT 7"),
    ("ADD duration_unit", "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS duration_unit VARCHAR(20) DEFAULT 'Jours'"),
    ("ADD is_trial", "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS is_trial BOOLEAN DEFAULT TRUE"),
    ("SET duration_days default", f"ALTER TABLE licenses ALTER COLUMN duration_days SET DEFAULT {DEFAULT_TRIAL_DAYS}"),
    ("ADD admin_unblocked", "ALTER TABLE device_attempts ADD COLUMN IF NOT EXISTS admin_unblocked BOOLEAN DEFAULT FALSE"),
    ("ADD admin_unblocked_at", "ALTER TABLE device_attempts ADD COLUMN IF NOT EXISTS admin_unblocked_at TIMESTAMP"),
    ("ADD max_attempts_allowed", "ALTER TABLE device_attempts ADD COLUMN IF NOT EXISTS max_attempts_allowed INTEGER DEFAULT 1"),

    ("ADD index device_attempts.phone",
     "CREATE INDEX IF NOT EXISTS ix_device_attempts_phone "
     "ON device_attempts (phone_number)"),

    ("NORMALIZE all devices quota to 1",
     "UPDATE device_attempts SET max_attempts_allowed = 1 "
     "WHERE admin_unblocked = FALSE "
     "AND (max_attempts_allowed IS NULL OR max_attempts_allowed > 1)"),

    ("BLOCK devices that used their only attempt",
     "UPDATE device_attempts SET is_blocked = TRUE, "
     "block_reason = COALESCE(NULLIF(block_reason, ''), "
     "'Quota atteint (1/1) - politique 1 essai/appareil') "
     "WHERE admin_unblocked = FALSE "
     "AND attempts_count >= 1 "
     "AND max_attempts_allowed <= 1"),

    ("RELINK licenses to devices (via phone)",
     "DO $$ "
     "DECLARE lic RECORD; dev RECORD; devs JSONB; "
     "BEGIN "
     "  FOR lic IN SELECT id, phone_number, device_uuid FROM licenses "
     "             WHERE device_uuid IS NULL OR device_uuid = '[]' OR device_uuid = '' "
     "  LOOP "
     "    SELECT device_id INTO dev FROM device_attempts "
     "    WHERE phone_number = lic.phone_number LIMIT 1; "
     "    IF dev.device_id IS NOT NULL THEN "
     "      devs := jsonb_build_array(dev.device_id); "
     "      UPDATE licenses SET device_uuid = devs::text "
     "      WHERE id = lic.id; "
     "    END IF; "
     "  END LOOP; "
     "END $$;"),
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

print("[startup] Migrations terminées.")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
class SelfRegisterPhoneRequest(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    phone_number: str = Field(..., min_length=6, max_length=50)
    organization: Optional[str] = Field("", max_length=150)
    device_id: str = Field(..., min_length=8, max_length=255)


class FlutterVerifyRequest(BaseModel):
    key: str = Field(..., min_length=8, max_length=32)
    device_id: str = Field(..., min_length=8, max_length=255)
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    organization: Optional[str] = ""


class AdminCreateLicenseRequest(BaseModel):
    phone_number: str = Field(..., min_length=6, max_length=50)
    duration_val: int = Field(..., ge=1, le=9999)
    duration_unit: str = Field(..., max_length=20)
    max_devices: int = Field(1, ge=1, le=50)
    is_active: Optional[bool] = False


# ✅ MODIFIÉ : ajout de duration_mode ("add" | "replace")
class AdminUpdateLicenseRequest(BaseModel):
    phone_number: Optional[str] = None
    user_name: Optional[str] = None
    organization: Optional[str] = None
    max_devices: Optional[int] = Field(None, ge=1, le=50)
    is_active: Optional[bool] = None
    extend_duration_val: Optional[int] = Field(None, ge=0, le=9999)
    extend_duration_unit: Optional[str] = None
    duration_mode: Optional[str] = Field("add", pattern="^(add|replace)$")


class NewsCreateRequest(BaseModel):
    title: str = Field(..., max_length=255)
    summary: str = Field(..., max_length=2000)
    content: Optional[str] = ""
    category: str = Field("news", max_length=50)
    version: Optional[str] = None
    download_url: Optional[str] = None


class DeviceUpdateRequest(BaseModel):
    is_blocked: Optional[bool] = None
    notes: Optional[str] = None
    add_attempts: Optional[int] = Field(None, ge=0, le=100)
    set_max_attempts: Optional[int] = Field(None, ge=0, le=100)
    grant_full_access: Optional[bool] = None
    duration_val: Optional[int] = Field(None, ge=1, le=9999)
    duration_unit: Optional[str] = None


# ✅ MODIFIÉ : ajout de duration_mode
class GrantFullAccessRequest(BaseModel):
    duration_val: int = Field(..., ge=1, le=9999)
    duration_unit: str = Field(..., max_length=20)
    duration_mode: Optional[str] = Field("replace", pattern="^(add|replace)$")


# ✅ MODIFIÉ : ajout de duration_mode
class ResetToTrialRequest(BaseModel):
    duration_val: int = Field(..., ge=1, le=9999)
    duration_unit: str = Field(..., max_length=20)
    duration_mode: Optional[str] = Field("replace", pattern="^(add|replace)$")


# ═══════════════════════════════════════════════════════════
# HELPER : durée → jours
# ═══════════════════════════════════════════════════════════
def _duration_to_days(val: int, unit: str) -> int:
    u = (unit or "").lower()
    if "mois" in u:
        return val * 30
    if "an" in u:
        return val * 365
    if "jour" in u:
        return val
    if "heure" in u:
        return max(1, math.ceil(val / 24))
    return val


# ═══════════════════════════════════════════════════════════
# HELPER : trouver une licence par device_id
# ═══════════════════════════════════════════════════════════
def _find_license_by_device(device_id: str, db: Session):
    try:
        clean_device = (device_id or "").strip().upper()
        if not clean_device:
            return None

        all_lics = db.query(LicenseKey).order_by(LicenseKey.id.desc()).all()

        now = get_utc_now()
        for lic in all_lics:
            try:
                raw = str(lic.device_uuid or "[]")
                if raw in ("", "REVOKED"):
                    continue
                devices = json.loads(raw)
                if not isinstance(devices, list):
                    continue
                normalized = [str(d).strip().upper() for d in devices]
                if clean_device in normalized:
                    is_expired = bool(lic.expires_at and now > lic.expires_at)
                    if lic.is_active and not is_expired:
                        return lic
            except Exception:
                continue

        for lic in all_lics:
            try:
                raw = str(lic.device_uuid or "[]")
                if raw in ("", "REVOKED"):
                    continue
                devices = json.loads(raw)
                if not isinstance(devices, list):
                    continue
                normalized = [str(d).strip().upper() for d in devices]
                if clean_device in normalized:
                    return lic
            except Exception:
                continue
    except Exception as e:
        print(f"[_find_license_by_device ERROR] {e}")
    return None


def _update_license_user_info(lic: LicenseKey, req, changed_log: list):
    new_phone = (getattr(req, "phone_number", "") or "").strip()
    old_phone = (lic.phone_number or "").strip()
    if new_phone and new_phone != old_phone:
        lic.phone_number = new_phone
        changed_log.append(f"tel: {old_phone or '∅'} → {new_phone}")

    new_first = (getattr(req, "first_name", "") or "").strip()
    old_first = (lic.first_name or "").strip()
    if new_first and new_first != old_first:
        lic.first_name = new_first
        changed_log.append(f"prénom: {old_first or '∅'} → {new_first}")

    new_last = (getattr(req, "last_name", "") or "").strip()
    old_last = (lic.last_name or "").strip()
    if new_last and new_last != old_last:
        lic.last_name = new_last
        changed_log.append(f"nom: {old_last or '∅'} → {new_last}")

    new_org = (getattr(req, "organization", "") or "").strip()
    old_org = (lic.organization or "").strip()
    if new_org and new_org != old_org:
        lic.organization = new_org
        changed_log.append(f"org: {old_org or '∅'} → {new_org}")


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
        "default_initial_quota": DEFAULT_INITIAL_QUOTA,
        "trial_max_tables": TRIAL_MAX_TABLES,
        "trial_max_rows": TRIAL_MAX_ROWS_PER_TABLE,
        "trial_allow_export": TRIAL_ALLOW_EXPORT,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


# ═══════════════════════════════════════════════════════════
# ROUTE : DEMANDE / RENOUVELLEMENT DE CLÉ
# ═══════════════════════════════════════════════════════════
@app.post("/api/license/request-key")
@app.post("/api/license/request-key/")
def request_license_key(req: SelfRegisterPhoneRequest, db: Session = Depends(get_db)):
    try:
        clean_phone = req.phone_number.strip().replace(" ", "")
        clean_device = req.device_id.strip().upper()

        if not clean_device:
            raise HTTPException(400, "Identifiant d'appareil requis.")
        if not clean_phone or len(clean_phone) < 6:
            raise HTTPException(400, "Numéro de téléphone invalide.")

        check_rate_limit(clean_device)

        existing_device_lic = _find_license_by_device(clean_device, db)

        if existing_device_lic:
            now = get_utc_now()
            is_expired = bool(
                existing_device_lic.expires_at and now > existing_device_lic.expires_at
            )
            is_revoked = (
                not existing_device_lic.is_active
                or str(existing_device_lic.device_uuid or "") == "REVOKED"
            )

            if not is_expired and not is_revoked:
                changed = []
                _update_license_user_info(existing_device_lic, req, changed)

                if changed:
                    print(f"[REQUEST-KEY] Device {clean_device} : "
                          f"mise à jour → {', '.join(changed)}")
                else:
                    print(f"[REQUEST-KEY] Device {clean_device} : "
                          f"clé existante renvoyée (rien à changer)")

                existing_dev = db.query(DeviceAttempt).filter(
                    DeviceAttempt.device_id == clean_device
                ).first()
                if existing_dev:
                    existing_dev.phone_number = clean_phone
                    existing_dev.last_attempt_at = now

                db.commit()

                return {
                    "status": "success",
                    "message": "Clé existante renvoyée pour cet appareil.",
                    "license_key": existing_device_lic.key,
                    "trial_days": existing_device_lic.duration_days or DEFAULT_TRIAL_DAYS,
                    "is_trial": bool(existing_device_lic.is_trial),
                }

            print(f"[REQUEST-KEY] Device {clean_device} : ancienne clé "
                  f"{'expirée' if is_expired else 'révoquée'} "
                  f"→ nouvelle clé demandée")

            dev_trace = db.query(DeviceAttempt).filter(
                DeviceAttempt.device_id == clean_device
            ).with_for_update().first()

            if not dev_trace:
                print(f"[REQUEST-KEY] Device {clean_device} : clé orpheline "
                      f"sans trace → REFUSÉ")
                raise HTTPException(
                    403,
                    "Cet appareil est marqué comme inconnu par le serveur. "
                    "Contactez l'administrateur pour réinitialiser votre accès."
                )

            max_allowed = dev_trace.max_attempts_allowed or 0
            attempts = dev_trace.attempts_count or 0

            if dev_trace.is_blocked or attempts >= max_allowed:
                admin_lic = db.query(LicenseKey).filter(
                    LicenseKey.phone_number == clean_phone,
                    LicenseKey.is_active == True,
                    LicenseKey.is_trial == False,
                ).first()

                if admin_lic:
                    try:
                        devs = json.loads(admin_lic.device_uuid or "[]")
                    except Exception:
                        devs = []
                    if clean_device not in devs:
                        devs.append(clean_device)
                        admin_lic.device_uuid = json.dumps(devs)

                    dev_trace.is_blocked = False
                    dev_trace.block_reason = ""
                    dev_trace.admin_unblocked = True
                    dev_trace.admin_unblocked_at = get_utc_now()

                    db.commit()
                    print(f"[REQUEST-KEY] Device {clean_device} débloqué "
                          f"car licence admin {admin_lic.key} trouvée")
                    return {
                        "status": "success",
                        "message": "Licence active trouvée.",
                        "license_key": admin_lic.key,
                        "trial_days": 0,
                        "is_trial": False,
                    }

                dev_trace.is_blocked = True
                dev_trace.last_attempt_at = now
                if not dev_trace.block_reason:
                    dev_trace.block_reason = (
                        f"Quota atteint ({attempts}/{max_allowed})"
                    )
                db.commit()

                print(f"[REQUEST-KEY] REFUSÉ : quota atteint "
                      f"({attempts}/{max_allowed}) pour {clean_device}")

                raise HTTPException(
                    403,
                    "Cet appareil a déjà utilisé toutes ses tentatives autorisées. "
                    "Contactez l'administrateur."
                )

            part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
            new_key = f"{part1}-{part2}-{part3}-{part4}"

            new_lic = LicenseKey(
                key=new_key,
                phone_number=clean_phone,
                first_name=(req.first_name or "").strip(),
                last_name=(req.last_name or "").strip(),
                organization=(req.organization or "").strip(),
                is_active=True,
                is_trial=True,
                device_uuid=json.dumps([clean_device]),
                max_devices=1,
                duration_days=DEFAULT_TRIAL_DAYS,
                duration_val=DEFAULT_TRIAL_DAYS,
                duration_unit=DEFAULT_TRIAL_UNIT,
                created_at=now,
            )
            db.add(new_lic)

            dev_trace.attempts_count = attempts + 1
            dev_trace.phone_number = clean_phone
            dev_trace.last_attempt_at = now

            if dev_trace.attempts_count >= max_allowed:
                dev_trace.is_blocked = True
                dev_trace.block_reason = (
                    f"Quota atteint ({dev_trace.attempts_count}/{max_allowed})"
                )
            else:
                dev_trace.is_blocked = False
                dev_trace.block_reason = ""

            try:
                old_devs = json.loads(existing_device_lic.device_uuid or "[]")
                if isinstance(old_devs, list) and clean_device in old_devs:
                    old_devs.remove(clean_device)
                    existing_device_lic.device_uuid = json.dumps(old_devs)
            except Exception:
                pass

            db.commit()
            db.refresh(new_lic)

            return {
                "status": "success",
                "message": "Nouvelle clé d'essai accordée (renouvellement).",
                "license_key": new_key,
                "trial_days": DEFAULT_TRIAL_DAYS,
                "is_trial": True,
            }

        admin_lic = db.query(LicenseKey).filter(
            LicenseKey.phone_number == clean_phone,
            LicenseKey.is_active == True,
            LicenseKey.is_trial == False,
        ).first()

        if admin_lic:
            dev_check = db.query(DeviceAttempt).filter(
                DeviceAttempt.device_id == clean_device
            ).first()

            if dev_check and dev_check.is_blocked:
                dev_check.is_blocked = False
                dev_check.block_reason = ""
                dev_check.admin_unblocked = True
                dev_check.admin_unblocked_at = get_utc_now()

            try:
                devs = json.loads(admin_lic.device_uuid or "[]")
            except Exception:
                devs = []
            if clean_device not in devs:
                devs.append(clean_device)
                admin_lic.device_uuid = json.dumps(devs)

            db.commit()

            return {
                "status": "success",
                "message": "Licence active trouvée.",
                "license_key": admin_lic.key,
                "trial_days": 0,
                "is_trial": False,
            }

        existing_device = db.query(DeviceAttempt).filter(
            DeviceAttempt.device_id == clean_device
        ).with_for_update().first()

        if existing_device:
            max_allowed = existing_device.max_attempts_allowed or 1
            attempts_count = existing_device.attempts_count or 0

            if existing_device.is_blocked or attempts_count >= max_allowed:
                admin_lic = db.query(LicenseKey).filter(
                    LicenseKey.phone_number == clean_phone,
                    LicenseKey.is_active == True,
                    LicenseKey.is_trial == False,
                ).first()

                if admin_lic:
                    try:
                        devs = json.loads(admin_lic.device_uuid or "[]")
                    except Exception:
                        devs = []
                    if clean_device not in devs:
                        devs.append(clean_device)
                        admin_lic.device_uuid = json.dumps(devs)

                    existing_device.is_blocked = False
                    existing_device.block_reason = ""
                    existing_device.admin_unblocked = True
                    existing_device.admin_unblocked_at = get_utc_now()

                    db.commit()
                    return {
                        "status": "success",
                        "message": "Licence active trouvée.",
                        "license_key": admin_lic.key,
                        "trial_days": 0,
                        "is_trial": False,
                    }

                existing_device.is_blocked = True
                existing_device.last_attempt_at = get_utc_now()
                if not existing_device.block_reason:
                    existing_device.block_reason = (
                        f"Quota atteint ({attempts_count}/{max_allowed})"
                    )
                db.commit()

                raise HTTPException(
                    403,
                    "Cet appareil a déjà utilisé toutes ses tentatives autorisées. "
                    "Contactez l'administrateur."
                )

            part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
            license_key = f"{part1}-{part2}-{part3}-{part4}"

            new_lic = LicenseKey(
                key=license_key,
                phone_number=clean_phone,
                first_name=(req.first_name or "").strip(),
                last_name=(req.last_name or "").strip(),
                organization=(req.organization or "").strip(),
                is_active=True,
                is_trial=True,
                device_uuid=json.dumps([clean_device]),
                max_devices=1,
                duration_days=DEFAULT_TRIAL_DAYS,
                duration_val=DEFAULT_TRIAL_DAYS,
                duration_unit=DEFAULT_TRIAL_UNIT,
                created_at=get_utc_now()
            )
            db.add(new_lic)

            existing_device.attempts_count = attempts_count + 1
            existing_device.phone_number = clean_phone
            existing_device.last_attempt_at = get_utc_now()

            remaining = max_allowed - existing_device.attempts_count
            if existing_device.attempts_count >= max_allowed:
                existing_device.is_blocked = True
                existing_device.block_reason = (
                    f"Quota atteint ({existing_device.attempts_count}/{max_allowed})"
                )
            else:
                existing_device.is_blocked = False
                existing_device.block_reason = ""

            db.commit()
            db.refresh(new_lic)
            return {
                "status": "success",
                "message": "Nouvel essai accordé.",
                "license_key": license_key,
                "trial_days": DEFAULT_TRIAL_DAYS,
                "is_trial": True,
            }

        existing_lic_by_phone = db.query(LicenseKey).filter(
            LicenseKey.phone_number == clean_phone
        ).first()

        if existing_lic_by_phone:
            if not existing_lic_by_phone.is_active:
                raise HTTPException(
                    403,
                    "Une licence existe déjà pour ce numéro mais elle est désactivée. "
                    "Contactez l'administrateur."
                )

            try:
                devs = json.loads(existing_lic_by_phone.device_uuid or "[]")
            except Exception:
                devs = []

            max_dev = existing_lic_by_phone.max_devices or 1
            if len(devs) < max_dev:
                devs.append(clean_device)
                existing_lic_by_phone.device_uuid = json.dumps(devs)

                new_attempt = DeviceAttempt(
                    device_id=clean_device,
                    phone_number=clean_phone,
                    attempts_count=0,
                    max_attempts_allowed=0,
                    is_blocked=False,
                    admin_unblocked=False,
                    first_attempt_at=get_utc_now(),
                    last_attempt_at=get_utc_now(),
                )
                db.add(new_attempt)
                db.commit()

                return {
                    "status": "success",
                    "message": "Clé existante liée à cet appareil.",
                    "license_key": existing_lic_by_phone.key,
                    "trial_days": existing_lic_by_phone.duration_days or DEFAULT_TRIAL_DAYS,
                    "is_trial": bool(existing_lic_by_phone.is_trial),
                }
            else:
                new_attempt = DeviceAttempt(
                    device_id=clean_device,
                    phone_number=clean_phone,
                    attempts_count=0,
                    max_attempts_allowed=0,
                    is_blocked=True,
                    block_reason="Limite d'appareils atteinte pour ce numéro",
                    admin_unblocked=False,
                    first_attempt_at=get_utc_now(),
                    last_attempt_at=get_utc_now(),
                )
                db.add(new_attempt)
                db.commit()

                raise HTTPException(
                    403,
                    "Une clé existe déjà pour ce numéro mais la limite "
                    "d'appareils est atteinte. Contactez l'administrateur."
                )

        new_attempt = DeviceAttempt(
            device_id=clean_device,
            phone_number=clean_phone,
            attempts_count=1,
            max_attempts_allowed=DEFAULT_INITIAL_QUOTA,
            is_blocked=(1 >= DEFAULT_INITIAL_QUOTA),
            block_reason=(
                f"Quota atteint (1/{DEFAULT_INITIAL_QUOTA})"
                if DEFAULT_INITIAL_QUOTA <= 1 else ""
            ),
            admin_unblocked=False,
            first_attempt_at=get_utc_now(),
            last_attempt_at=get_utc_now(),
        )
        db.add(new_attempt)

        part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
        license_key = f"{part1}-{part2}-{part3}-{part4}"

        new_lic = LicenseKey(
            key=license_key,
            phone_number=clean_phone,
            first_name=(req.first_name or "").strip(),
            last_name=(req.last_name or "").strip(),
            organization=(req.organization or "").strip(),
            is_active=True,
            is_trial=True,
            device_uuid=json.dumps([clean_device]),
            max_devices=1,
            duration_days=DEFAULT_TRIAL_DAYS,
            duration_val=DEFAULT_TRIAL_DAYS,
            duration_unit=DEFAULT_TRIAL_UNIT,
            created_at=get_utc_now()
        )
        db.add(new_lic)
        db.commit()
        db.refresh(new_lic)

        return {
            "status": "success",
            "message": "Clé d'essai générée avec succès !",
            "license_key": license_key,
            "trial_days": DEFAULT_TRIAL_DAYS,
            "is_trial": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[REQUEST-KEY ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur DB: {str(e)}")


# ═══════════════════════════════════════════════════════════
# ROUTE : VÉRIFICATION / ACTIVATION
# ═══════════════════════════════════════════════════════════
@app.post("/api/license/verify")
@app.post("/api/license/verify/")
def verify_or_activate_flutter(req: FlutterVerifyRequest, db: Session = Depends(get_db)):
    try:
        clean_key = req.key.strip().upper()
        clean_device = req.device_id.strip().upper()

        license_entry = db.query(LicenseKey).filter(LicenseKey.key == clean_key).first()

        if not license_entry:
            raise HTTPException(
                404,
                "Clé de licence introuvable. Vérifiez les caractères saisis."
            )

        if not license_entry.is_active or license_entry.device_uuid == "REVOKED":
            raise HTTPException(
                403,
                "Cette licence a été désactivée ou révoquée. "
                "Contactez l'administrateur pour la réactiver."
            )

        now = get_utc_now()
        if license_entry.expires_at and now > license_entry.expires_at:
            raise HTTPException(
                403,
                "Cette licence a expiré. "
                "Contactez l'administrateur pour la renouveler."
            )

        if not license_entry.activated_at:
            license_entry.activated_at = now
            license_entry.expires_at = now + timedelta(
                days=license_entry.duration_days or DEFAULT_TRIAL_DAYS
            )

        changed = []
        _update_license_user_info(license_entry, req, changed)
        if changed:
            print(f"[VERIFY] Licence {clean_key} : mise à jour → {', '.join(changed)}")

        raw_dev = license_entry.device_uuid
        if raw_dev in (None, "", "REVOKED"):
            devices = []
        else:
            try:
                devices = json.loads(raw_dev) if isinstance(raw_dev, str) else []
                if not isinstance(devices, list):
                    devices = []
            except Exception:
                devices = []

        devices_normalized = [str(d).strip().upper() for d in devices]

        max_dev = license_entry.max_devices or 1

        if clean_device not in devices_normalized:
            if len(devices_normalized) >= max_dev:
                raise HTTPException(
                    403,
                    f"Limite d'appareils atteinte pour cette clé "
                    f"({len(devices_normalized)}/{max_dev}). Contactez l'administrateur."
                )
            devices.append(clean_device)
            license_entry.device_uuid = json.dumps(devices)
        else:
            pass

        dev_trace = db.query(DeviceAttempt).filter(
            DeviceAttempt.device_id == clean_device
        ).first()

        if dev_trace:
            dev_trace.last_attempt_at = get_utc_now()

            if dev_trace.is_blocked:
                dev_trace.is_blocked = False
                dev_trace.block_reason = ""
                dev_trace.admin_unblocked = True
                dev_trace.admin_unblocked_at = get_utc_now()
        else:
            dev_trace = DeviceAttempt(
                device_id=clean_device,
                phone_number=license_entry.phone_number or "",
                attempts_count=0,
                max_attempts_allowed=0,
                is_blocked=False,
                admin_unblocked=False,
                first_attempt_at=get_utc_now(),
                last_attempt_at=get_utc_now(),
            )
            db.add(dev_trace)

        db.commit()

        is_trial = bool(license_entry.is_trial)
        return {
            "status": "valid",
            "phone_number": license_entry.phone_number or "",
            "expires_at": license_entry.expires_at.strftime("%Y-%m-%d %H:%M:%S") if license_entry.expires_at else None,
            "is_trial": is_trial,
            "limits": {
                "max_tables": TRIAL_MAX_TABLES if is_trial else 999999,
                "max_rows_per_table": TRIAL_MAX_ROWS_PER_TABLE if is_trial else 999999,
                "allow_export": (not is_trial) or TRIAL_ALLOW_EXPORT,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[VERIFY ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur DB: {str(e)}")


# ═══════════════════════════════════════════════════════════
# ROUTES ADMIN — LICENCES
# ═══════════════════════════════════════════════════════════
@app.get("/api/admin/licenses", dependencies=[Depends(require_admin_key)])
@app.get("/api/admin/licenses/", dependencies=[Depends(require_admin_key)])
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
                "duration_days": item.duration_days or DEFAULT_TRIAL_DAYS,
                "duration_val": item.duration_val if item.duration_val is not None else DEFAULT_TRIAL_DAYS,
                "duration_unit": item.duration_unit or DEFAULT_TRIAL_UNIT,
                "is_active": bool(item.is_active and str(item.device_uuid) != "REVOKED"),
                "is_trial": bool(item.is_trial),
                "created_at": item.created_at.isoformat() if item.created_at else "",
                "expires_at": item.expires_at.isoformat() if item.expires_at else None
            })
        return results
    except Exception as err:
        print(f"[ADMIN-LIST ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur SQL/Python: {str(err)}")


@app.post("/api/admin/licenses/create", dependencies=[Depends(require_admin_key)])
@app.post("/api/admin/licenses/create/", dependencies=[Depends(require_admin_key)])
def create_admin_license(req: AdminCreateLicenseRequest, db: Session = Depends(get_db)):
    try:
        part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
        license_key = f"{part1}-{part2}-{part3}-{part4}"

        days = _duration_to_days(req.duration_val, req.duration_unit)

        new_lic = LicenseKey(
            key=license_key,
            phone_number=req.phone_number.strip().replace(" ", ""),
            is_active=bool(req.is_active) if req.is_active is not None else False,
            is_trial=True,
            device_uuid="[]",
            max_devices=req.max_devices,
            duration_days=days,
            duration_val=req.duration_val,
            duration_unit=req.duration_unit,
            created_at=get_utc_now()
        )
        db.add(new_lic)
        db.commit()
        db.refresh(new_lic)
        return {
            "id": new_lic.id,
            "key": license_key,
            "phone_number": new_lic.phone_number,
            "duration_val": new_lic.duration_val,
            "duration_unit": new_lic.duration_unit,
            "duration_days": new_lic.duration_days,
            "is_active": new_lic.is_active,
            "is_trial": new_lic.is_trial
        }
    except Exception as e:
        db.rollback()
        print(f"[ADMIN-CREATE ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur DB: {str(e)}")


# ✅ MODIFIÉ : support de duration_mode ("add" | "replace")
@app.put("/api/admin/licenses/{key}", dependencies=[Depends(require_admin_key)])
@app.put("/api/admin/licenses/{key}/", dependencies=[Depends(require_admin_key)])
def update_admin_license_full(key: str, req: AdminUpdateLicenseRequest, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    if req.phone_number is not None:
        lic.phone_number = req.phone_number.strip().replace(" ", "")
    if req.organization is not None:
        lic.organization = req.organization.strip() if req.organization != "—" else ""
    if req.user_name is not None and req.user_name != "Non activé":
        parts = req.user_name.strip().split(" ", 1)
        lic.first_name = parts[0] if len(parts) > 0 else ""
        lic.last_name = parts[1] if len(parts) > 1 else ""
    if req.max_devices is not None:
        lic.max_devices = int(req.max_devices)
    if req.is_active is not None:
        lic.is_active = bool(req.is_active)

    # ✅ Durée : gère "add" (cumul) ou "replace" (remplacement)
    if req.extend_duration_val is not None and req.extend_duration_val > 0 and req.extend_duration_unit:
        val = int(req.extend_duration_val)
        new_days = _duration_to_days(val, req.extend_duration_unit)
        mode = (req.duration_mode or "add").lower()

        if mode == "replace":
            # 🔄 Remplacement complet
            lic.duration_val = val
            lic.duration_unit = req.extend_duration_unit
            lic.duration_days = new_days
        else:
            # ➕ Ajout au cumul
            total_days = (lic.duration_days or 0) + new_days
            lic.duration_days = total_days
            lic.duration_val = total_days
            lic.duration_unit = "Jours"

        # Recalcul de l'expiration si déjà activée
        if lic.activated_at:
            lic.expires_at = lic.activated_at + timedelta(days=lic.duration_days)

    db.commit()
    db.refresh(lic)
    return {
        "status": "success",
        "message": "Licence mise à jour avec succès !",
        "key": lic.key,
        "is_active": lic.is_active,
        "max_devices": lic.max_devices,
        "duration_val": lic.duration_val,
        "duration_unit": lic.duration_unit,
        "duration_days": lic.duration_days,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }


@app.post("/api/admin/licenses/{key}/status", dependencies=[Depends(require_admin_key)])
def toggle_admin_license_status(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")
    lic.is_active = not lic.is_active
    db.commit()
    return {"status": "success", "is_active": lic.is_active}


def _unblock_associated_devices(lic: LicenseKey, db: Session):
    try:
        devices = json.loads(lic.device_uuid or "[]")
        if not isinstance(devices, list):
            return
        for dev_id in devices:
            dev = db.query(DeviceAttempt).filter(
                DeviceAttempt.device_id == dev_id
            ).first()
            if dev and dev.is_blocked:
                dev.is_blocked = False
                dev.block_reason = ""
                dev.admin_unblocked = True
                dev.admin_unblocked_at = get_utc_now()
    except Exception as e:
        print(f"[_unblock_associated_devices] {e}")


# ✅ MODIFIÉ : support de duration_mode ("add" | "replace")
@app.post("/api/admin/licenses/{key}/grant-full-access", dependencies=[Depends(require_admin_key)])
@app.post("/api/admin/licenses/{key}/grant-full-access/", dependencies=[Depends(require_admin_key)])
def grant_full_access(key: str, req: GrantFullAccessRequest, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    new_days = _duration_to_days(req.duration_val, req.duration_unit)
    mode = (req.duration_mode or "replace").lower()

    lic.is_active = True
    lic.is_trial = False

    if mode == "add":
        # ➕ Ajout : cumule avec la durée existante
        total_days = (lic.duration_days or 0) + new_days
        lic.duration_days = total_days
        lic.duration_val = total_days
        lic.duration_unit = "Jours"
    else:
        # 🔄 Remplacement : remplace complètement
        lic.duration_val = req.duration_val
        lic.duration_unit = req.duration_unit
        lic.duration_days = new_days

    now = get_utc_now()
    lic.activated_at = now
    lic.expires_at = now + timedelta(days=lic.duration_days)

    _unblock_associated_devices(lic, db)

    db.commit()
    db.refresh(lic)

    return {
        "status": "success",
        "message": f"Accès complet accordé ({mode} : {lic.duration_days} jours).",
        "key": lic.key,
        "is_active": lic.is_active,
        "is_trial": lic.is_trial,
        "duration_val": lic.duration_val,
        "duration_unit": lic.duration_unit,
        "duration_days": lic.duration_days,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }


# ✅ MODIFIÉ : support de duration_mode ("add" | "replace")
@app.post("/api/admin/licenses/{key}/reset-to-trial", dependencies=[Depends(require_admin_key)])
@app.post("/api/admin/licenses/{key}/reset-to-trial/", dependencies=[Depends(require_admin_key)])
def reset_license_to_trial(key: str, req: ResetToTrialRequest, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    new_days = _duration_to_days(req.duration_val, req.duration_unit)
    mode = (req.duration_mode or "replace").lower()

    lic.is_active = True
    lic.is_trial = True

    if mode == "add":
        total_days = (lic.duration_days or 0) + new_days
        lic.duration_days = total_days
        lic.duration_val = total_days
        lic.duration_unit = "Jours"
    else:
        lic.duration_val = req.duration_val
        lic.duration_unit = req.duration_unit
        lic.duration_days = new_days

    now = get_utc_now()
    lic.activated_at = now
    lic.expires_at = now + timedelta(days=lic.duration_days)

    _unblock_associated_devices(lic, db)

    db.commit()
    db.refresh(lic)

    return {
        "status": "success",
        "message": f"Licence remise en essai ({mode} : {lic.duration_days} jours).",
        "key": lic.key,
        "is_active": lic.is_active,
        "is_trial": lic.is_trial,
        "duration_val": lic.duration_val,
        "duration_unit": lic.duration_unit,
        "duration_days": lic.duration_days,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }


@app.post("/api/admin/licenses/{key}/reset-devices", dependencies=[Depends(require_admin_key)])
def reset_admin_license_devices(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    try:
        devs = json.loads(lic.device_uuid or "[]")
        if not isinstance(devs, list):
            devs = []
    except Exception:
        devs = []

    lic.device_uuid = "[]"
    lic.activated_at = None
    lic.expires_at = None

    deleted_count = 0
    for dev_id in devs:
        dev = db.query(DeviceAttempt).filter(
            DeviceAttempt.device_id == dev_id
        ).first()
        if dev:
            db.delete(dev)
            deleted_count += 1

    db.commit()

    return {
        "status": "success",
        "message": f"Appareils dissociés ({deleted_count} trace(s) supprimée(s))."
    }


@app.delete("/api/admin/licenses/{key}", dependencies=[Depends(require_admin_key)])
def delete_admin_license(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")
    db.delete(lic)
    db.commit()
    return {"status": "success", "message": "Licence supprimée définitivement."}


# ═══════════════════════════════════════════════════════════
# ROUTES ADMIN — APPAREILS
# ═══════════════════════════════════════════════════════════
@app.get("/api/admin/devices", dependencies=[Depends(require_admin_key)])
@app.get("/api/admin/devices/", dependencies=[Depends(require_admin_key)])
def get_admin_devices(db: Session = Depends(get_db)):
    try:
        devices = db.query(DeviceAttempt).order_by(DeviceAttempt.last_attempt_at.desc()).all()
        return [
            {
                "id": d.id,
                "device_id": d.device_id,
                "phone_number": d.phone_number,
                "attempts_count": d.attempts_count,
                "max_attempts_allowed": d.max_attempts_allowed or 0,
                "is_blocked": d.is_blocked,
                "block_reason": d.block_reason or "",
                "notes": d.notes or "",
                "admin_unblocked": bool(d.admin_unblocked),
                "admin_unblocked_at": d.admin_unblocked_at.isoformat() if d.admin_unblocked_at else None,
                "first_attempt_at": d.first_attempt_at.isoformat() if d.first_attempt_at else "",
                "last_attempt_at": d.last_attempt_at.isoformat() if d.last_attempt_at else "",
            }
            for d in devices
        ]
    except Exception as err:
        print(f"[ADMIN-DEVICES ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur SQL: {str(err)}")


def _grant_full_access_to_device(
    dev: DeviceAttempt,
    duration_val: int,
    duration_unit: str,
    db: Session,
) -> dict:
    clean_device = dev.device_id.strip().upper()
    days = _duration_to_days(duration_val, duration_unit)
    now = get_utc_now()

    existing_lic = _find_license_by_device(clean_device, db)

    if existing_lic:
        existing_lic.is_active = True
        existing_lic.is_trial = False
        existing_lic.duration_val = duration_val
        existing_lic.duration_unit = duration_unit
        existing_lic.duration_days = days
        existing_lic.activated_at = now
        existing_lic.expires_at = now + timedelta(days=days)
        existing_lic.device_uuid = json.dumps([clean_device])
        lic = existing_lic
        action = "updated"
    else:
        part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
        new_key = f"{part1}-{part2}-{part3}-{part4}"

        lic = LicenseKey(
            key=new_key,
            phone_number=dev.phone_number or "",
            is_active=True,
            is_trial=False,
            device_uuid=json.dumps([clean_device]),
            max_devices=1,
            duration_days=days,
            duration_val=duration_val,
            duration_unit=duration_unit,
            created_at=now,
            activated_at=now,
            expires_at=now + timedelta(days=days),
        )
        db.add(lic)
        action = "created"

    dev.is_blocked = False
    dev.block_reason = ""
    dev.admin_unblocked = True
    dev.admin_unblocked_at = now

    db.commit()
    db.refresh(lic)

    return {
        "action": action,
        "key": lic.key,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        "duration_val": duration_val,
        "duration_unit": duration_unit,
        "duration_days": days,
    }


@app.put("/api/admin/devices/{device_id}", dependencies=[Depends(require_admin_key)])
@app.put("/api/admin/devices/{device_id}/", dependencies=[Depends(require_admin_key)])
def update_admin_device(device_id: str, req: DeviceUpdateRequest, db: Session = Depends(get_db)):
    dev = db.query(DeviceAttempt).filter(
        DeviceAttempt.device_id == device_id.strip().upper()
    ).first()
    if not dev:
        raise HTTPException(404, "Appareil introuvable.")

    was_blocked = dev.is_blocked
    log_lines = []
    full_access_info = None

    if req.grant_full_access is True:
        duration_val = req.duration_val or 7
        duration_unit = req.duration_unit or "Jours"
        full_access_info = _grant_full_access_to_device(
            dev, duration_val, duration_unit, db
        )
        log_lines.append(
            f"ACCÈS COMPLET {duration_val} {duration_unit} → clé {full_access_info['key']}"
        )

    if req.set_max_attempts is not None:
        dev.max_attempts_allowed = max(0, req.set_max_attempts)
        dev.is_blocked = False
        dev.block_reason = ""
        dev.admin_unblocked = True
        dev.admin_unblocked_at = get_utc_now()
        log_lines.append(f"quota={dev.max_attempts_allowed} (absolu)")

    elif req.add_attempts is not None and req.add_attempts > 0:
        current = dev.max_attempts_allowed or 0
        dev.max_attempts_allowed = current + req.add_attempts
        dev.is_blocked = False
        dev.block_reason = ""
        dev.admin_unblocked = True
        dev.admin_unblocked_at = get_utc_now()
        log_lines.append(f"+{req.add_attempts} → quota={dev.max_attempts_allowed}")

    if req.is_blocked is not None:
        new_blocked = bool(req.is_blocked)
        if new_blocked and not was_blocked:
            dev.is_blocked = True
            dev.admin_unblocked = False
            dev.admin_unblocked_at = None
            if not dev.block_reason:
                dev.block_reason = "Bloqué manuellement par l'administrateur"
            log_lines.append("BLOQUÉ")
        elif not new_blocked and was_blocked:
            current = dev.max_attempts_allowed or 0
            dev.max_attempts_allowed = current + 1
            dev.is_blocked = False
            dev.admin_unblocked = True
            dev.admin_unblocked_at = get_utc_now()
            dev.block_reason = ""
            log_lines.append(f"DÉBLOQUÉ (+1 → quota={dev.max_attempts_allowed})")
        else:
            dev.is_blocked = new_blocked

    if req.notes is not None:
        dev.notes = req.notes

    db.commit()
    db.refresh(dev)

    if log_lines:
        print(f"[ADMIN] Device {device_id} : {', '.join(log_lines)}")

    return {
        "status": "success",
        "device_id": dev.device_id,
        "is_blocked": dev.is_blocked,
        "attempts_count": dev.attempts_count,
        "max_attempts_allowed": dev.max_attempts_allowed or 0,
        "admin_unblocked": bool(dev.admin_unblocked),
        "notes": dev.notes,
        "full_access": full_access_info,
    }


@app.delete("/api/admin/devices/{device_id}", dependencies=[Depends(require_admin_key)])
def delete_admin_device(device_id: str, db: Session = Depends(get_db)):
    clean_dev = device_id.strip().upper()

    dev = db.query(DeviceAttempt).filter(
        DeviceAttempt.device_id == clean_dev
    ).first()
    if not dev:
        raise HTTPException(404, "Appareil introuvable.")

    all_lics = db.query(LicenseKey).all()
    cleaned = 0
    for lic in all_lics:
        try:
            raw = str(lic.device_uuid or "[]")
            if raw in ("", "REVOKED"):
                continue
            devices = json.loads(raw)
            if isinstance(devices, list):
                normalized = [str(d).strip().upper() for d in devices]
                if clean_dev in normalized:
                    devices = [d for d in devices
                               if str(d).strip().upper() != clean_dev]
                    lic.device_uuid = json.dumps(devices)
                    cleaned += 1
        except Exception:
            continue

    db.delete(dev)
    db.commit()

    return {
        "status": "success",
        "message": f"Trace d'appareil supprimée (retiré de {cleaned} licence(s))."
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
