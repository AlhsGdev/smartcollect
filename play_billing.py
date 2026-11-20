"""Google Play prices from the admin console.

The subscription itself (its product, its base plans "monthly" and
"yearly") is created once in the Play Console. Here the prices of those
base plans are changed through the Google Play Developer API, so the admin
sets a price in USD and Google shows it, in each country's currency, to the
new subscribers (and the app shows what Google returns).

Environment (on Render, never in the repository):
  PLAY_PACKAGE_NAME            app id, e.g. com.alhousseny.smartcollect
  GOOGLE_PLAY_SERVICE_ACCOUNT  the service account's JSON key (whole text)
  PLAY_PRODUCT_PREFIX          optional, default "tablyo_": the product of
                               the plan "pro" is "tablyo_pro"
Base plan ids: "monthly" and "yearly".

Existing subscribers keep their price: Google asks them to accept a rise
(see basePlans.migratePrices), which is not done here.
"""
import json
import os

import requests

API = "https://androidpublisher.googleapis.com/androidpublisher/v3"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"


class PlayError(Exception):
    """A Play problem, in words the admin can read."""


def package_name() -> str:
    return os.environ.get("PLAY_PACKAGE_NAME", "").strip()


def product_prefix() -> str:
    return os.environ.get("PLAY_PRODUCT_PREFIX", "tablyo_")


def product_id(plan_code: str) -> str:
    return product_prefix() + plan_code


def configured() -> bool:
    return bool(package_name() and os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT", "").strip())


def money(amount_usd: float) -> dict:
    """USD amount as Google's Money (units + nanos)."""
    cents = round(float(amount_usd) * 100)
    return {"currencyCode": "USD", "units": str(cents // 100), "nanos": (cents % 100) * 10_000_000}


def _token() -> str:
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request

        info = json.loads(os.environ["GOOGLE_PLAY_SERVICE_ACCOUNT"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        creds.refresh(Request())
        return creds.token
    except KeyError:
        raise PlayError("GOOGLE_PLAY_SERVICE_ACCOUNT n'est pas défini sur le serveur.")
    except Exception as e:  # bad key, no network...
        raise PlayError(f"Connexion à Google impossible : {e}")


def _call(method: str, path: str, token: str, **kw):
    r = requests.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=30, **kw)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        hint = ""
        if r.status_code in (401, 403):
            hint = " (le compte de service a-t-il accès à l'app dans la Play Console, avec le droit de gérer les abonnements ?)"
        if r.status_code == 404:
            hint = " (l'abonnement existe-t-il dans la Play Console, avec ce même identifiant ?)"
        raise PlayError(f"Google a répondu {r.status_code} : {msg}{hint}")
    return r.json() if r.text else {}


def apply_prices(subscription: dict, converted: dict, base_plan_id: str) -> int:
    """Puts the converted prices into the base plan of [subscription];
    returns how many countries were changed. [converted] is Google's
    convertRegionPrices answer."""
    prices = converted.get("convertedRegionPrices", {})
    changed = 0
    for bp in subscription.get("basePlans", []):
        if bp.get("basePlanId") != base_plan_id:
            continue
        for rc in bp.get("regionalConfigs", []):
            p = prices.get(rc.get("regionCode"))
            if p and p.get("price"):
                rc["price"] = p["price"]
                changed += 1
        other = converted.get("convertedOtherRegionsPrice")
        if other and bp.get("otherRegionsConfig"):
            bp["otherRegionsConfig"]["usdPrice"] = other.get("usdPrice")
            bp["otherRegionsConfig"]["eurPrice"] = other.get("eurPrice")
    return changed


def sync_plan(plan_code: str, prices_usd: dict, dry_run: bool = False) -> dict:
    """Sends [prices_usd] ({"monthly": 5.0, "yearly": 40.0}) to Google for
    the product of [plan_code]. Returns a summary for the admin."""
    if not configured():
        raise PlayError("Google Play n'est pas configuré sur le serveur (PLAY_PACKAGE_NAME, GOOGLE_PLAY_SERVICE_ACCOUNT).")
    pkg, product = package_name(), product_id(plan_code)
    token = _token()
    sub = _call("GET", f"/applications/{pkg}/subscriptions/{product}", token)
    summary = {}
    for base_plan_id, usd in prices_usd.items():
        if usd is None or usd <= 0:
            continue
        conv = _call("POST", f"/applications/{pkg}/pricing:convertRegionPrices", token, json={"price": money(usd)})
        summary[base_plan_id] = apply_prices(sub, conv, base_plan_id)
    if dry_run or not any(summary.values()):
        return {"product": product, "countries": summary, "sent": False}
    _call(
        "PATCH",
        f"/applications/{pkg}/subscriptions/{product}",
        token,
        params={"updateMask": "basePlans", "regionsVersion.version": "2022/02"},
        json=sub,
    )
    return {"product": product, "countries": summary, "sent": True}


# ------------------------------------------------------------- purchases
from datetime import datetime, timezone


def _naive_utc(rfc3339):
    """Google's RFC 3339 time as the naive UTC the database keeps."""
    if not rfc3339:
        return None
    d = datetime.fromisoformat(rfc3339.replace("Z", "+00:00"))
    return d.astimezone(timezone.utc).replace(tzinfo=None)


def parse_subscription(data: dict) -> dict:
    """What matters of Google's SubscriptionPurchaseV2: the product bought,
    its base plan ("monthly" / "yearly"), when it ends and whether it is
    still giving access."""
    items = data.get("lineItems") or []
    if not items:
        raise PlayError("Achat sans article : jeton invalide.")
    item = items[0]
    state = data.get("subscriptionState", "")
    return {
        "product_id": item.get("productId", ""),
        "base_plan_id": (item.get("offerDetails") or {}).get("basePlanId", ""),
        "expires_at": _naive_utc(item.get("expiryTime")),
        "state": state.replace("SUBSCRIPTION_STATE_", "").lower(),
        "auto_renewing": bool((item.get("autoRenewingPlan") or {}).get("autoRenewEnabled")),
        "order_id": data.get("latestOrderId", ""),
        "acknowledged": data.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
        # Access while active, cancelled (until the end), or in grace period.
        "gives_access": state in (
            "SUBSCRIPTION_STATE_ACTIVE",
            "SUBSCRIPTION_STATE_CANCELED",
            "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        ),
    }


def verify_purchase(purchase_token: str) -> dict:
    """Asks Google about a purchase token (the app never decides)."""
    if not configured():
        raise PlayError("Google Play n'est pas configuré sur le serveur.")
    token = _token()
    data = _call("GET", f"/applications/{package_name()}/purchases/subscriptionsv2/tokens/{purchase_token}", token)
    return parse_subscription(data)


def plan_code_of(product_id: str) -> str:
    """"tablyo_pro" -> "pro" ("" if the product is not one of ours)."""
    prefix = product_prefix()
    return product_id[len(prefix):] if product_id.startswith(prefix) else ""
