"""Privacy policy of the Tablyo app (public page required by Google
Play and by the Google sign-in consent screen). French by default,
English with ?lang=en. The contact address comes from PRIVACY_CONTACT_EMAIL.
"""

import html
import os

UPDATED_FR = "18 novembre 2026"
UPDATED_EN = "November 18, 2026"
PUBLISHER = "Alhss Gdev"


def _contact() -> str:
    return os.getenv("PRIVACY_CONTACT_EMAIL", "").strip()


_FR = """
<h1>Politique de confidentialité de Tablyo</h1>
<p class="meta">Éditeur : {publisher} · Dernière mise à jour : {updated}</p>

<p>Tablyo est une application de collecte et d'organisation de données
(tables, fiches, rendez-vous, documents). Cette page explique quelles données
sont traitées, pourquoi, et quels sont vos droits.</p>

<h2>1. Vos données de travail restent sur votre appareil</h2>
<p>Les tables, lignes, photos, signatures, fiches, documents, rendez-vous et
réglages que vous saisissez sont enregistrés <strong>uniquement sur votre
appareil</strong>. Ils ne sont pas envoyés à nos serveurs.</p>

<h2>2. Données de compte</h2>
<p>Pour créer et utiliser votre compte, nos serveurs conservent :</p>
<ul>
  <li>votre adresse e-mail et votre prénom (et, si vous les indiquez, votre nom et
  votre numéro de téléphone) ;</li>
  <li>votre mot de passe, sous forme chiffrée (jamais en clair) ;</li>
  <li>les appareils connectés à votre compte (identifiant technique, nom et type
  d'appareil, date de dernière connexion), pour appliquer le nombre d'appareils
  de votre formule ;</li>
  <li>votre formule (essai, abonnement), sa date de fin et, en cas d'achat sur
  Google Play, le jeton d'achat permettant de vérifier l'abonnement.</li>
</ul>
<p>Ces données servent uniquement à vous identifier, à protéger votre compte
et à gérer votre formule. Les serveurs sont hébergés par Render (application)
et Neon (base de données). Les e-mails de réinitialisation du mot de passe sont
envoyés via Brevo.</p>

<h2>3. Sauvegardes</h2>
<p>Les sauvegardes que vous exportez sont des fichiers <strong>chiffrés pour
votre compte</strong> : seul votre compte peut les restaurer.</p>
<p>Si vous activez la <strong>sauvegarde Google Drive</strong>, l'application
demande uniquement l'accès à son propre espace privé dans votre Google Drive
(autorisation « drive.appdata »). Elle ne voit pas vos autres fichiers. Les
sauvegardes y sont envoyées chiffrées ; nous n'y avons pas accès. Vous pouvez
retirer cet accès à tout moment depuis l'application ou depuis votre compte
Google (myaccount.google.com/permissions).</p>
<p>L'utilisation des informations reçues des API Google respecte le
<a href="https://developers.google.com/terms/api-services-user-data-policy">Règlement
sur les données utilisateur des services d'API Google</a>, y compris les
exigences d'utilisation limitée.</p>

<h2>4. Ce que nous ne faisons pas</h2>
<ul>
  <li>Pas de publicité, pas de revente de données, pas de profilage.</li>
  <li>Pas d'outil de statistiques ni de suivi publicitaire dans l'application.</li>
  <li>Le verrouillage par empreinte ou visage utilise le système de votre
  appareil : aucune donnée biométrique ne nous est transmise.</li>
</ul>

<h2>5. Autorisations de l'application</h2>
<ul>
  <li><strong>Internet</strong> : connexion au compte, actualités, Google Drive.</li>
  <li><strong>Notifications et alarmes</strong> : rappels de vos rendez-vous et
  appels, programmés sur l'appareil.</li>
  <li><strong>Caméra, photos, fichiers</strong> : seulement quand vous ajoutez une
  photo, un logo ou importez un fichier.</li>
</ul>

<h2>6. Conservation et suppression</h2>
<p>Les données de compte sont conservées tant que votre compte existe. Vous
pouvez supprimer votre compte et les données associées à tout moment, depuis
l'application (Compte › Supprimer mon compte) ou depuis la page
<a href="/delete-account">Supprimer mon compte</a> ; la suppression est
immédiate. Vous pouvez aussi la demander à l'adresse ci-dessous. Les données
de travail sur votre appareil disparaissent quand vous désinstallez
l'application ; vos sauvegardes Google Drive peuvent être supprimées depuis
Google Drive (Paramètres › Gérer les applications).</p>

<h2>7. Sécurité</h2>
<p>Les échanges avec nos serveurs sont chiffrés (HTTPS). Les mots de passe sont
stockés chiffrés et les sauvegardes sont chiffrées pour votre compte.</p>

<h2>8. Enfants</h2>
<p>Tablyo est destinée à un usage professionnel ou associatif et ne
s'adresse pas aux enfants de moins de 13 ans.</p>

<h2>9. Contact</h2>
<p>Pour toute question ou demande (accès, correction, suppression) :
{contact}.</p>

<h2>10. Modifications</h2>
<p>Cette politique peut évoluer ; la date de mise à jour figure en haut de la
page.</p>
"""

_EN = """
<h1>Tablyo privacy policy</h1>
<p class="meta">Publisher: {publisher} · Last updated: {updated}</p>

<p>Tablyo is an app to collect and organise data (tables, sheets,
appointments, documents). This page explains which data is processed, why, and
your rights.</p>

<h2>1. Your work data stays on your device</h2>
<p>The tables, rows, photos, signatures, sheets, documents, appointments and
settings you enter are stored <strong>only on your device</strong>. They are not
sent to our servers.</p>

<h2>2. Account data</h2>
<p>To create and use your account, our servers keep:</p>
<ul>
  <li>your e-mail address and first name (and, if you give them, your last name
  and phone number);</li>
  <li>your password, hashed (never in clear text);</li>
  <li>the devices signed in to your account (technical identifier, device name and
  type, last sign-in date), to apply your plan's device limit;</li>
  <li>your plan (trial, subscription), its end date and, for Google Play purchases,
  the purchase token used to verify the subscription.</li>
</ul>
<p>This data is only used to identify you, protect your account and manage your
plan. Servers are hosted by Render (application) and Neon (database). Password
reset e-mails are sent through Brevo.</p>

<h2>3. Backups</h2>
<p>Backups you export are files <strong>encrypted for your account</strong>: only
your account can restore them.</p>
<p>If you turn on <strong>Google Drive backup</strong>, the app only asks for
access to its own private space in your Google Drive ("drive.appdata"). It
cannot see your other files. Backups are sent there encrypted; we cannot read
them. You can withdraw this access at any time from the app or from your Google
account (myaccount.google.com/permissions).</p>
<p>Tablyo's use of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google
API Services User Data Policy</a>, including the Limited Use requirements.</p>

<h2>4. What we do not do</h2>
<ul>
  <li>No ads, no data selling, no profiling.</li>
  <li>No analytics or ad tracking in the app.</li>
  <li>Fingerprint or face unlock uses your device's system: no biometric data is
  sent to us.</li>
</ul>

<h2>5. App permissions</h2>
<ul>
  <li><strong>Internet</strong>: account, news, Google Drive.</li>
  <li><strong>Notifications and alarms</strong>: reminders of your appointments and
  calls, scheduled on the device.</li>
  <li><strong>Camera, photos, files</strong>: only when you add a photo or a logo,
  or import a file.</li>
</ul>

<h2>6. Retention and deletion</h2>
<p>Account data is kept as long as your account exists. You can delete your
account and its data at any time, from the app (Account › Delete my account) or
from the <a href="/delete-account?lang=en">Delete my account</a> page; deletion
is immediate. You can also ask for it at the address below. Work data on your device is removed when you uninstall the app;
your Google Drive backups can be deleted from Google Drive (Settings › Manage
apps).</p>

<h2>7. Security</h2>
<p>Exchanges with our servers are encrypted (HTTPS). Passwords are stored hashed
and backups are encrypted for your account.</p>

<h2>8. Children</h2>
<p>Tablyo is meant for professional or community use and is not directed
at children under 13.</p>

<h2>9. Contact</h2>
<p>For any question or request (access, correction, deletion): {contact}.</p>

<h2>10. Changes</h2>
<p>This policy may change; the update date is shown at the top of the page.</p>
"""

_PAGE = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{margin:0;background:#F3F5F4;color:#0B1F2A;font:16px/1.6 Inter,system-ui,
-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:28px;line-height:1.25;margin:0 0 6px}}
h2{{font-size:19px;margin:28px 0 8px;color:#0E7C86}}
.meta{{color:#4A5B66;margin:0 0 20px}}
a{{color:#0E7C86}}
nav{{margin-bottom:18px}}
@media (prefers-color-scheme:dark){{body{{background:#0B1F2A;color:#E8EEF0}}
.meta{{color:#A9B8C0}}h2,a{{color:#5CC3CC}}}}
</style></head>
<body><main>
<nav><a href="?lang={other}">{switch}</a></nav>
{body}
</main></body></html>"""


def privacy_page(lang: str = "fr") -> str:
    en = lang.lower().startswith("en")
    contact = _contact()
    if contact:
        mail = html.escape(contact)
        contact_html = f'<a href="mailto:{mail}">{mail}</a>'
    else:
        contact_html = "—"
    body = (_EN if en else _FR).format(
        publisher=PUBLISHER,
        updated=UPDATED_EN if en else UPDATED_FR,
        contact=contact_html,
    )
    return _PAGE.format(
        lang="en" if en else "fr",
        title="Tablyo – Privacy policy" if en
        else "Tablyo – Politique de confidentialité",
        other="fr" if en else "en",
        switch="Version française" if en else "English version",
        body=body,
    )


_DELETE = {
    "fr": {
        "title": "Tablyo – Supprimer mon compte",
        "h1": "Supprimer mon compte Tablyo",
        "intro": (
            "Vous pouvez supprimer votre compte depuis l'application "
            "(Compte › Supprimer mon compte) ou ici. La suppression est "
            "<strong>définitive</strong> : votre compte, votre formule et la "
            "liste de vos appareils sont effacés de nos serveurs, aussitôt."
        ),
        "kept": (
            "Vos données de travail ne sont pas sur nos serveurs : elles restent "
            "sur vos appareils jusqu'à la désinstallation de l'app. Vos "
            "sauvegardes Google Drive se suppriment depuis Google Drive "
            "(Paramètres › Gérer les applications). Un abonnement Google Play "
            "s'arrête depuis Google Play (Paiements et abonnements)."
        ),
        "email": "Adresse e-mail du compte",
        "password": "Mot de passe",
        "confirm": "Je comprends que la suppression est définitive.",
        "button": "Supprimer définitivement mon compte",
        "done": "Votre compte a été supprimé.",
        "wrong": "E-mail ou mot de passe incorrect.",
        "error": "La suppression n'a pas abouti. Réessayez plus tard.",
        "other": "en",
        "switch": "English version",
    },
    "en": {
        "title": "Tablyo – Delete my account",
        "h1": "Delete my Tablyo account",
        "intro": (
            "You can delete your account from the app (Account › Delete my "
            "account) or here. Deletion is <strong>permanent</strong>: your "
            "account, your plan and the list of your devices are erased from "
            "our servers at once."
        ),
        "kept": (
            "Your work data is not on our servers: it stays on your devices "
            "until you uninstall the app. Your Google Drive backups are deleted "
            "from Google Drive (Settings › Manage apps). A Google Play "
            "subscription is cancelled in Google Play (Payments & "
            "subscriptions)."
        ),
        "email": "Account e-mail",
        "password": "Password",
        "confirm": "I understand that deletion is permanent.",
        "button": "Permanently delete my account",
        "done": "Your account has been deleted.",
        "wrong": "Wrong e-mail or password.",
        "error": "Deletion failed. Please try again later.",
        "other": "fr",
        "switch": "Version française",
    },
}

_DELETE_PAGE = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{margin:0;background:#F3F5F4;color:#0B1F2A;font:16px/1.6 Inter,system-ui,
-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:560px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:26px;line-height:1.25;margin:0 0 12px}}
p{{margin:0 0 14px}} .muted{{color:#4A5B66;font-size:15px}}
a{{color:#0E7C86}} nav{{margin-bottom:18px}}
label{{display:block;font-weight:600;margin:14px 0 6px}}
input[type=email],input[type=password]{{width:100%;box-sizing:border-box;
min-height:44px;padding:10px 12px;border:1px solid #C9D3D7;border-radius:14px;
font:inherit;background:#fff;color:#0B1F2A}}
.check{{display:flex;gap:10px;align-items:flex-start;font-weight:400}}
.check input{{width:20px;height:20px;margin-top:3px}}
button{{margin-top:18px;min-height:48px;width:100%;border:0;border-radius:14px;
background:#C0392B;color:#fff;font:600 16px Inter,system-ui,sans-serif;cursor:pointer}}
button:disabled{{opacity:.5;cursor:default}}
#msg{{margin-top:16px;font-weight:600}}
@media (prefers-color-scheme:dark){{body{{background:#0B1F2A;color:#E8EEF0}}
.muted{{color:#A9B8C0}}a{{color:#5CC3CC}}}}
</style></head>
<body><main>
<nav><a href="?lang={other}">{switch}</a> · <a href="/privacy?lang={lang}">Privacy</a></nav>
<h1>{h1}</h1>
<p>{intro}</p>
<p class="muted">{kept}</p>
<form id="f">
<label for="e">{email}</label>
<input id="e" type="email" autocomplete="username" required>
<label for="p">{password}</label>
<input id="p" type="password" autocomplete="current-password" required>
<label class="check"><input id="c" type="checkbox" required> {confirm}</label>
<button id="b" type="submit">{button}</button>
<div id="msg" role="status"></div>
</form>
<script>
document.getElementById('f').addEventListener('submit', async function (ev) {{
  ev.preventDefault();
  var b = document.getElementById('b'), m = document.getElementById('msg');
  b.disabled = true; m.textContent = '';
  try {{
    var r = await fetch('/api/account/delete-by-password', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{email: document.getElementById('e').value,
                            password: document.getElementById('p').value}})
    }});
    if (r.ok) {{ m.textContent = {done}; document.getElementById('f').reset(); return; }}
    m.textContent = r.status === 401 ? {wrong} : {error};
  }} catch (_) {{ m.textContent = {error}; }}
  b.disabled = false;
}});
</script>
</main></body></html>"""


def delete_account_page_html(lang: str = "fr") -> str:
    import json

    key = "en" if lang.lower().startswith("en") else "fr"
    t = _DELETE[key]
    return _DELETE_PAGE.format(
        lang=key,
        title=t["title"],
        h1=t["h1"],
        intro=t["intro"],
        kept=t["kept"],
        email=t["email"],
        password=t["password"],
        confirm=t["confirm"],
        button=t["button"],
        done=json.dumps(t["done"]),
        wrong=json.dumps(t["wrong"]),
        error=json.dumps(t["error"]),
        other=t["other"],
        switch=t["switch"],
    )
