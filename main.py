# main.py
# ... (tout le début reste identique jusqu'à la ligne "SCHÉMAS PYDANTIC")

import os
import json
import secrets
import time
import traceback
import re
import hashlib
import random    # ✅ AJOUTÉ
import string    # ✅ AJOUTÉ
from datetime import datetime, timedelta, timezone
from typing import Optional, List

# ... (tout le code jusqu'aux schémas Pydantic reste identique)

# ═══════════════════════════════════════════════════════════
# SCHÉMAS PYDANTIC
# ═══════════════════════════════════════════════════════════

# ... (les autres schémas existants) ...

# ✅ NOUVEAUX SCHÉMAS
class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)

class ResetPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    otp_code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=100)


# ═══════════════════════════════════════════════════════════
# ROUTES AUTH
# ═══════════════════════════════════════════════════════════
# ... (register, login, verify-session, logout existants) ...

# ✅ ROUTE 1 : Demander la réinitialisation
@app.post("/api/auth/forgot-password")
def auth_forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = _normalize_email(req.email)
    account = db.query(Account).filter(Account.email == email).first()

    # Sécurité : on renvoie un succès même si l'email n'existe pas
    if not account:
        return {"status": "success", "message": "Si cet email existe, un code a été envoyé."}

    # Générer un code à 6 chiffres
    otp_code = ''.join(random.choices(string.digits, k=6))
    expires_at = get_utc_now() + timedelta(minutes=15)

    def set_config(key, val):
        row = db.query(AppConfig).filter(AppConfig.key == key).first()
        if row: row.value = val
        else: db.add(AppConfig(key=key, value=val))

    set_config(f"reset_code_{email}", otp_code)
    set_config(f"reset_expires_{email}", expires_at.isoformat())
    db.commit()

    # ⚠️ ICI : ENVOI DE L'EMAIL
    # Pour tester, on affiche le code dans les logs Render.
    print(f"🔑 [RESET PASSWORD] Code pour {email} : {otp_code}")
    
    # (Optionnel) Intégration avec un service d'email (SendGrid, Resend, Brevo)
    # send_email(to=email, subject="Code SmartCollect", body=f"Code: {otp_code}")

    return {"status": "success", "message": "Code envoyé."}


# ✅ ROUTE 2 : Valider le code et changer le mot de passe
@app.post("/api/auth/reset-password")
def auth_reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    email = _normalize_email(req.email)
    account = db.query(Account).filter(Account.email == email).first()
    if not account:
        raise HTTPException(404, "Compte introuvable.")

    code_row = db.query(AppConfig).filter(AppConfig.key == f"reset_code_{email}").first()
    exp_row = db.query(AppConfig).filter(AppConfig.key == f"reset_expires_{email}").first()

    if not code_row or code_row.value != req.otp_code:
        raise HTTPException(400, "Code invalide.")

    if not exp_row:
        raise HTTPException(400, "Code expiré.")

    try:
        exp_time = datetime.fromisoformat(exp_row.value)
        if get_utc_now() > exp_time:
            raise HTTPException(400, "Code expiré.")
    except ValueError:
        raise HTTPException(400, "Erreur de date d'expiration.")

    # Mettre à jour le mot de passe
    account.password_hash = _hash_password(req.new_password)

    # Supprimer le code utilisé
    db.delete(code_row)
    db.delete(exp_row)

    # Déconnecter tous les appareils (sécurité)
    db.query(AccountDevice).filter(AccountDevice.account_id == account.id).update({"is_active": False})

    db.commit()
    return {"status": "success", "message": "Mot de passe mis à jour."}


# ... (le reste du fichier reste identique : routes devices, upgrade, plans, admin, news, etc.)
