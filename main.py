import os
import json
import secrets
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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
    """Trace des tentatives d'essai par device_id matériel."""
    __tablename__ = "device_attempts"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(255), unique=True, index=True, nullable=False)
    phone_number = Column(String(50), nullable=False)
    first_attempt_at = Column(DateTime, default=get_utc_now)
    last_attempt_at = Column(DateTime, default=get_utc_now)
    attempts_count = Column(Integer, default=1)
    
    # ✅ NOUVEAU : quota max d'essais autorisés
    # Par défaut : 1 (un seul essai gratuit)
    # L'admin peut augmenter ce nombre pour autoriser plus d'essais
    max_attempts_allowed = Column(Integer, default=1)
    
    is_blocked = Column(Boolean, default=False)
    block_reason = Column(String(255), default="")
    notes = Column(Text, default="")
    
    # Legacy (conservé pour compatibilité)
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


# ==========================================
# MIGRATIONS ROBUSTES
# ==========================================
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
    # ✅ NOUVELLE COLONNE : quota d'essais autorisés
    ("ADD max_attempts_allowed", "ALTER TABLE device_attempts ADD COLUMN IF NOT EXISTS max_attempts_allowed INTEGER DEFAULT 1"),
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
    device_id: str


class FlutterVerifyRequest(BaseModel):
    key: str
    device_id: str
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    organization: Optional[str] = ""


class AdminCreateLicenseRequest(BaseModel):
    phone_number: str
    duration_val: int
    duration_unit: str
    max_devices: int = 1
    is_active: Optional[bool] = False


class AdminUpdateLicenseRequest(BaseModel):
    phone_number: Optional[str] = None
    user_name: Optional[str] = None
    organization: Optional[str] = None
    max_devices: Optional[int] = None
    is_active: Optional[bool] = None
    extend_duration_val: Optional[int] = None
    extend_duration_unit: Optional[str] = None


class NewsCreateRequest(BaseModel):
    title: str
    summary: str
    content: Optional[str] = ""
    category: str = "news"
    version: Optional[str] = None
    download_url: Optional[str] = None


class DeviceUpdateRequest(BaseModel):
    is_blocked: Optional[bool] = None
    notes: Optional[str] = None
    # ✅ NOUVEAU : nombre de tentatives à ajouter au quota
    add_attempts: Optional[int] = None


class GrantFullAccessRequest(BaseModel):
    duration_val: int
    duration_unit: str


class ResetToTrialRequest(BaseModel):
    duration_val: int
    duration_unit: str


# ==========================================
# ROUTES PUBLIQUES
# ==========================================
@app.get("/")
def home():
    return {
        "status": "online",
        "database": "Neon PostgreSQL",
        "service": "SmartCollect Unified API",
        "default_trial_days": DEFAULT_TRIAL_DAYS,
        "trial_max_tables": TRIAL_MAX_TABLES,
        "trial_max_rows": TRIAL_MAX_ROWS_PER_TABLE,
        "trial_allow_export": TRIAL_ALLOW_EXPORT,
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
    """
    RÈGLES :
    1. Device ne peut obtenir qu'autant d'essais que `max_attempts_allowed`.
    2. Par défaut : 1 seul essai (quota=1).
    3. Admin peut augmenter le quota pour autoriser plus d'essais.
    4. Réinstallation NE contourne PAS (device_id stable).
    5. Admin peut aussi créer une licence complète (pas de compteur).
    """
    try:
        clean_phone = req.phone_number.strip().replace(" ", "")
        clean_device = req.device_id.strip().upper()

        if not clean_device:
            raise HTTPException(400, "Identifiant d'appareil requis.")
        if not clean_phone or len(clean_phone) < 6:
            raise HTTPException(400, "Numéro de téléphone invalide.")

        # ═══════════════════════════════════════════════════════════
        # PRIORITÉ 0 : Licence ADMIN existante pour ce numéro ?
        # ═══════════════════════════════════════════════════════════
        admin_lic = db.query(LicenseKey).filter(
            LicenseKey.phone_number == clean_phone,
            LicenseKey.is_active == True,
            LicenseKey.is_trial == False,
        ).first()

        if admin_lic:
            print(f"[REQUEST-KEY] Licence admin trouvée pour {clean_phone}")
            return {
                "status": "success",
                "message": "Licence active trouvée.",
                "license_key": admin_lic.key,
                "trial_days": 0,
                "is_trial": False,
            }

        # ═══════════════════════════════════════════════════════════
        # PRIORITÉ 1 : Device déjà enregistré ?
        # ═══════════════════════════════════════════════════════════
        existing_device = db.query(DeviceAttempt).filter(
            DeviceAttempt.device_id == clean_device
        ).first()

        if existing_device:
            # Récupérer le quota (fallback à 1 si colonne absente)
            max_allowed = getattr(existing_device, 'max_attempts_allowed', 1) or 1
            attempts_count = existing_device.attempts_count or 0

            print(f"[REQUEST-KEY] Device {clean_device} : {attempts_count}/{max_allowed} tentatives")

            # ─────────────────────────────────────────────────────
            # CAS A : Quota atteint ou bloqué → refus
            # ─────────────────────────────────────────────────────
            if existing_device.is_blocked or attempts_count >= max_allowed:
                # Vérifier si une licence admin est associée
                admin_lic = db.query(LicenseKey).filter(
                    LicenseKey.phone_number == clean_phone,
                    LicenseKey.is_active == True,
                    LicenseKey.is_trial == False,
                ).first()

                if admin_lic:
                    return {
                        "status": "success",
                        "message": "Licence active trouvée.",
                        "license_key": admin_lic.key,
                        "trial_days": 0,
                        "is_trial": False,
                    }

                # Sinon → blocage automatique
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
                    "Contactez l'administrateur pour plus d'accès."
                )

            # ─────────────────────────────────────────────────────
            # CAS B : Quota disponible → on autorise un nouvel essai
            # ─────────────────────────────────────────────────────
            print(f"[REQUEST-KEY] Quota disponible ({attempts_count}/{max_allowed}) → nouvel essai")

            # Vérifier si une licence existe pour ce numéro
            existing_lic = db.query(LicenseKey).filter(
                LicenseKey.phone_number == clean_phone
            ).first()

            if existing_lic and existing_lic.is_active:
                existing_device.phone_number = clean_phone
                existing_device.last_attempt_at = get_utc_now()
                db.commit()

                return {
                    "status": "success",
                    "message": "Une clé existe déjà pour ce numéro.",
                    "license_key": existing_lic.key,
                    "trial_days": existing_lic.duration_days or DEFAULT_TRIAL_DAYS,
                    "is_trial": bool(existing_lic.is_trial),
                }

            # Créer une nouvelle licence d'essai
            part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
            license_key = f"{part1}-{part2}-{part3}-{part4}"

            new_lic = LicenseKey(
                key=license_key,
                phone_number=clean_phone,
                first_name=req.first_name.strip(),
                last_name=req.last_name.strip(),
                organization=req.organization.strip() if req.organization else "",
                is_active=True,
                is_trial=True,
                device_uuid="[]",
                max_devices=1,
                duration_days=DEFAULT_TRIAL_DAYS,
                duration_val=DEFAULT_TRIAL_DAYS,
                duration_unit=DEFAULT_TRIAL_UNIT,
                created_at=get_utc_now()
            )
            db.add(new_lic)

            # Incrémenter le compteur
            existing_device.attempts_count = attempts_count + 1
            existing_device.phone_number = clean_phone
            existing_device.last_attempt_at = get_utc_now()

            # Si on atteint le quota, on bloque
            if existing_device.attempts_count >= max_allowed:
                existing_device.is_blocked = True
                existing_device.block_reason = "Quota atteint après ce nouvel essai"
                print(f"[REQUEST-KEY] Quota atteint ({existing_device.attempts_count}/{max_allowed}) → device bloqué")

            db.commit()
            db.refresh(new_lic)

            print(f"[REQUEST-KEY] Nouvel essai accordé : {license_key}")
            return {
                "status": "success",
                "message": "Nouvel essai accordé.",
                "license_key": license_key,
                "trial_days": DEFAULT_TRIAL_DAYS,
                "is_trial": True,
            }

        # ═══════════════════════════════════════════════════════════
        # PRIORITÉ 2 : Device jamais vu + numéro déjà utilisé ?
        # ═══════════════════════════════════════════════════════════
        existing_lic = db.query(LicenseKey).filter(
            LicenseKey.phone_number == clean_phone
        ).first()

        if existing_lic:
            if not existing_lic.is_active:
                raise HTTPException(
                    403,
                    "Une licence existe déjà pour ce numéro mais elle "
                    "est désactivée. Contactez l'administrateur."
                )

            new_attempt = DeviceAttempt(
                device_id=clean_device,
                phone_number=clean_phone,
                attempts_count=1,
                max_attempts_allowed=1,
                is_blocked=False,
                admin_unblocked=False,
                first_attempt_at=get_utc_now(),
                last_attempt_at=get_utc_now(),
            )
            db.add(new_attempt)
            db.commit()

            return {
                "status": "success",
                "message": "Une clé existe déjà pour ce numéro.",
                "license_key": existing_lic.key,
                "trial_days": existing_lic.duration_days or DEFAULT_TRIAL_DAYS,
                "is_trial": bool(existing_lic.is_trial),
            }

        # ═══════════════════════════════════════════════════════════
        # PRIORITÉ 3 : Vraiment nouvel appareil + nouveau numéro
        # ═══════════════════════════════════════════════════════════
        new_attempt = DeviceAttempt(
            device_id=clean_device,
            phone_number=clean_phone,
            attempts_count=1,
            max_attempts_allowed=1,
            is_blocked=False,
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
            first_name=req.first_name.strip(),
            last_name=req.last_name.strip(),
            organization=req.organization.strip() if req.organization else "",
            is_active=True,
            is_trial=True,
            device_uuid="[]",
            max_devices=1,
            duration_days=DEFAULT_TRIAL_DAYS,
            duration_val=DEFAULT_TRIAL_DAYS,
            duration_unit=DEFAULT_TRIAL_UNIT,
            created_at=get_utc_now()
        )
        db.add(new_lic)
        db.commit()
        db.refresh(new_lic)

        print(f"[REQUEST-KEY] Nouvel essai (nouveau device) : {license_key}")
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


@app.post("/api/license/verify")
@app.post("/api/license/verify/")
def verify_or_activate_flutter(req: FlutterVerifyRequest, db: Session = Depends(get_db)):
    try:
        clean_key = req.key.strip().upper()
        license_entry = db.query(LicenseKey).filter(LicenseKey.key == clean_key).first()

        if not license_entry:
            raise HTTPException(404, "Clé de licence introuvable.")

        if not license_entry.is_active or license_entry.device_uuid == "REVOKED":
            raise HTTPException(403, "Cette licence a été désactivée ou révoquée.")

        now = get_utc_now()
        if license_entry.expires_at and now > license_entry.expires_at:
            raise HTTPException(403, "Cette licence a expiré.")

        if not license_entry.activated_at:
            license_entry.activated_at = now
            license_entry.expires_at = now + timedelta(
                days=license_entry.duration_days or DEFAULT_TRIAL_DAYS
            )

        try:
            devices = json.loads(license_entry.device_uuid or "[]") if isinstance(license_entry.device_uuid, str) else []
        except Exception:
            devices = []

        max_dev = license_entry.max_devices or 1
        if req.device_id not in devices:
            if len(devices) >= max_dev:
                raise HTTPException(403, "Limite d'appareils atteinte pour cette clé.")
            devices.append(req.device_id)
            license_entry.device_uuid = json.dumps(devices)

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


# ==========================================
# ROUTES ADMINISTRATION DES LICENCES
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


@app.post("/api/admin/licenses/create")
@app.post("/api/admin/licenses/create/")
def create_admin_license(req: AdminCreateLicenseRequest, db: Session = Depends(get_db)):
    try:
        part1, part2, part3, part4 = [secrets.token_hex(2).upper() for _ in range(4)]
        license_key = f"{part1}-{part2}-{part3}-{part4}"

        unit = req.duration_unit.lower()
        if "mois" in unit:
            days = req.duration_val * 30
        elif "an" in unit:
            days = req.duration_val * 365
        elif "jour" in unit:
            days = req.duration_val
        elif "heure" in unit:
            days = max(1, req.duration_val // 24)
        else:
            days = req.duration_val

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


@app.put("/api/admin/licenses/{key}")
@app.put("/api/admin/licenses/{key}/")
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

    if req.extend_duration_val and req.extend_duration_unit:
        val = int(req.extend_duration_val)
        unit = req.extend_duration_unit.lower()
        if "mois" in unit:
            extra_days = val * 30
        elif "an" in unit:
            extra_days = val * 365
        elif "jour" in unit:
            extra_days = val
        else:
            extra_days = val

        lic.duration_days = (lic.duration_days or DEFAULT_TRIAL_DAYS) + extra_days
        lic.duration_val = val
        lic.duration_unit = req.extend_duration_unit
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


@app.post("/api/admin/licenses/{key}/status")
def toggle_admin_license_status(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    lic.is_active = not lic.is_active
    db.commit()
    return {"status": "success", "is_active": lic.is_active}


@app.post("/api/admin/licenses/{key}/grant-full-access")
@app.post("/api/admin/licenses/{key}/grant-full-access/")
def grant_full_access(
    key: str,
    req: GrantFullAccessRequest,
    db: Session = Depends(get_db)
):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    unit = req.duration_unit.lower()
    if "mois" in unit:
        days = req.duration_val * 30
    elif "an" in unit:
        days = req.duration_val * 365
    elif "jour" in unit:
        days = req.duration_val
    elif "heure" in unit:
        days = max(1, req.duration_val // 24)
    else:
        days = req.duration_val

    lic.is_active = True
    lic.is_trial = False
    lic.duration_val = req.duration_val
    lic.duration_unit = req.duration_unit
    lic.duration_days = days

    now = get_utc_now()
    lic.activated_at = now
    lic.expires_at = now + timedelta(days=days)

    db.commit()
    db.refresh(lic)

    return {
        "status": "success",
        "message": f"Tous les accès accordés pour {req.duration_val} {req.duration_unit}.",
        "key": lic.key,
        "is_active": lic.is_active,
        "is_trial": lic.is_trial,
        "duration_val": lic.duration_val,
        "duration_unit": lic.duration_unit,
        "duration_days": lic.duration_days,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }


@app.post("/api/admin/licenses/{key}/reset-to-trial")
@app.post("/api/admin/licenses/{key}/reset-to-trial/")
def reset_license_to_trial(
    key: str,
    req: ResetToTrialRequest,
    db: Session = Depends(get_db)
):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    unit = req.duration_unit.lower()
    if "mois" in unit:
        days = req.duration_val * 30
    elif "an" in unit:
        days = req.duration_val * 365
    elif "jour" in unit:
        days = req.duration_val
    elif "heure" in unit:
        days = max(1, req.duration_val // 24)
    else:
        days = req.duration_val

    lic.is_active = True
    lic.is_trial = True
    lic.duration_val = req.duration_val
    lic.duration_unit = req.duration_unit
    lic.duration_days = days

    now = get_utc_now()
    lic.activated_at = now
    lic.expires_at = now + timedelta(days=days)

    db.commit()
    db.refresh(lic)

    return {
        "status": "success",
        "message": f"Licence remise en essai pour {req.duration_val} {req.duration_unit}.",
        "key": lic.key,
        "is_active": lic.is_active,
        "is_trial": lic.is_trial,
        "duration_val": lic.duration_val,
        "duration_unit": lic.duration_unit,
        "duration_days": lic.duration_days,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None
    }


@app.post("/api/admin/licenses/{key}/reset-devices")
def reset_admin_license_devices(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    lic.device_uuid = "[]"
    lic.activated_at = None
    lic.expires_at = None
    db.commit()
    return {"status": "success", "message": "Appareils dissociés."}


@app.delete("/api/admin/licenses/{key}")
def delete_admin_license(key: str, db: Session = Depends(get_db)):
    lic = db.query(LicenseKey).filter(LicenseKey.key == key.strip().upper()).first()
    if not lic:
        raise HTTPException(404, "Licence introuvable.")

    db.delete(lic)
    db.commit()
    return {"status": "success", "message": "Licence supprimée définitivement."}


# ═══════════════════════════════════════════════════════════
# GESTION DES APPAREILS
# ═══════════════════════════════════════════════════════════
@app.get("/api/admin/devices")
@app.get("/api/admin/devices/")
def get_admin_devices(db: Session = Depends(get_db)):
    try:
        devices = db.query(DeviceAttempt).order_by(
            DeviceAttempt.last_attempt_at.desc()
        ).all()
        return [
            {
                "id": d.id,
                "device_id": d.device_id,
                "phone_number": d.phone_number,
                "attempts_count": d.attempts_count,
                "max_attempts_allowed": getattr(d, 'max_attempts_allowed', 1) or 1,
                "is_blocked": d.is_blocked,
                "block_reason": d.block_reason or "",
                "notes": d.notes or "",
                "admin_unblocked": bool(d.admin_unblocked) if hasattr(d, 'admin_unblocked') else False,
                "admin_unblocked_at": d.admin_unblocked_at.isoformat() if hasattr(d, 'admin_unblocked_at') and d.admin_unblocked_at else None,
                "first_attempt_at": d.first_attempt_at.isoformat() if d.first_attempt_at else "",
                "last_attempt_at": d.last_attempt_at.isoformat() if d.last_attempt_at else "",
            }
            for d in devices
        ]
    except Exception as err:
        print(f"[ADMIN-DEVICES ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur SQL: {str(err)}")


@app.put("/api/admin/devices/{device_id}")
@app.put("/api/admin/devices/{device_id}/")
def update_admin_device(device_id: str, req: DeviceUpdateRequest, db: Session = Depends(get_db)):
    dev = db.query(DeviceAttempt).filter(
        DeviceAttempt.device_id == device_id.strip().upper()
    ).first()
    
    if not dev:
        raise HTTPException(404, "Appareil introuvable.")

    # ✅ NOUVEAU : mise à jour du quota (add_attempts)
    if req.add_attempts is not None and req.add_attempts > 0:
        current = getattr(dev, 'max_attempts_allowed', 1) or 1
        dev.max_attempts_allowed = current + req.add_attempts
        dev.is_blocked = False
        dev.block_reason = ""
        dev.admin_unblocked = True
        dev.admin_unblocked_at = get_utc_now()
        print(f"[ADMIN] Device {device_id} : quota augmenté de {req.add_attempts} → {dev.max_attempts_allowed}")
    
    # Déblocage classique (compatibilité ancien bouton)
    if req.is_blocked is not None:
        dev.is_blocked = bool(req.is_blocked)
        
        if req.is_blocked:
            # Blocage manuel
            dev.admin_unblocked = False
            dev.admin_unblocked_at = None
            if not dev.block_reason:
                dev.block_reason = "Bloqué manuellement par l'administrateur"
        else:
            # Déblocage → +1 tentative par défaut
            current = getattr(dev, 'max_attempts_allowed', 1) or 1
            dev.max_attempts_allowed = current + 1
            dev.admin_unblocked = True
            dev.admin_unblocked_at = get_utc_now()
            dev.block_reason = ""
            print(f"[ADMIN] Device {device_id} débloqué (+1 tentative → {dev.max_attempts_allowed})")
    
    if req.notes is not None:
        dev.notes = req.notes

    db.commit()
    db.refresh(dev)
    
    return {
        "status": "success",
        "device_id": dev.device_id,
        "is_blocked": dev.is_blocked,
        "attempts_count": dev.attempts_count,
        "max_attempts_allowed": getattr(dev, 'max_attempts_allowed', 1) or 1,
        "admin_unblocked": bool(dev.admin_unblocked) if hasattr(dev, 'admin_unblocked') else False,
        "notes": dev.notes
    }


@app.delete("/api/admin/devices/{device_id}")
def delete_admin_device(device_id: str, db: Session = Depends(get_db)):
    dev = db.query(DeviceAttempt).filter(DeviceAttempt.device_id == device_id.strip().upper()).first()
    if not dev:
        raise HTTPException(404, "Appareil introuvable.")

    db.delete(dev)
    db.commit()
    return {"status": "success", "message": "Trace d'appareil supprimée."}


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
        print(f"[NEWS ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"Erreur SQL News: {str(err)}")


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
        raise HTTPException(404, "Article introuvable.")
    db.delete(item)
    db.commit()
    return {"status": "success", "message": "Actualité supprimée."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
