"""
Billing Router — Creem checkout, customer portal, and webhooks.
Docs: https://docs.creem.io
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings
from app.core.dependencies import get_current_user, get_supabase_client
from app.services import creem
from app.services.creem import CreemError

router = APIRouter()


class CheckoutRequest(BaseModel):
    plan: str  # "pro" | "pro_yearly" | "byok" | "byok_yearly" | "team"


# Bare URL only. Creem returns 400 if success_url contains ? or #.
# After payment, Creem redirects here and appends checkout_id and subscription_id.
CHECKOUT_SUCCESS_URL = "https://www.toneloom.pro"
_LEGACY_SUCCESS_HOSTS = {
    "echowrite.app",
    "www.echowrite.app",
}


def _success_url() -> str:
    """Return a plain http(s) URL with no query string or fragment."""
    raw = (
        settings.CREEM_SUCCESS_URL
        or settings.FRONTEND_URL
        or CHECKOUT_SUCCESS_URL
    ).strip().strip("\"'")
    base = raw.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    host = base.split("://", 1)[-1].split("/", 1)[0].lower()
    if not base.startswith(("http://", "https://")) or host in _LEGACY_SUCCESS_HOSTS:
        return CHECKOUT_SUCCESS_URL
    return base


def _extract_id(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        return value.get("id")
    return None


def _period_end(subscription: Optional[dict]) -> Optional[str]:
    if not subscription:
        return None
    for key in ("current_period_end_date", "current_period_end", "next_transaction_date"):
        val = subscription.get(key)
        if val:
            return val
    return None


@router.post("/checkout")
async def create_checkout(
    req: CheckoutRequest,
    user=Depends(get_current_user),
    supabase=Depends(get_supabase_client),
):
    """Create a Creem checkout session and return the hosted checkout URL."""
    plan = (req.plan or "").lower().strip()
    if plan not in {"pro", "pro_yearly", "byok", "byok_yearly", "team"}:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {req.plan}")

    try:
        product_id = creem.product_id_for_plan(plan)
    except CreemError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    profile_result = (
        supabase.table("profiles")
        .select("email, stripe_customer_id")
        .eq("id", str(user.id))
        .single()
        .execute()
    )
    profile = profile_result.data or {}
    email = profile.get("email") or getattr(user, "email", None) or ""
    if not email:
        raise HTTPException(status_code=400, detail="Account email is required for checkout.")

    user_id = str(user.id)
    try:
        session = await creem.create_checkout(
            product_id=product_id,
            customer_email=email,
            success_url=_success_url(),
            request_id=user_id,
            metadata={
                "supabase_user_id": user_id,
                "plan": plan,
            },
        )
    except CreemError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    checkout_url = session.get("checkout_url") or session.get("checkoutUrl")
    if not checkout_url:
        raise HTTPException(status_code=502, detail="Creem did not return a checkout URL.")

    return {"checkout_url": checkout_url, "provider": "creem"}


@router.get("/subscription")
async def get_subscription(
    user=Depends(get_current_user),
    supabase=Depends(get_supabase_client),
):
    """Return the user's current subscription status."""
    result = (
        supabase.table("profiles")
        .select("plan, subscription_status, current_period_end, stripe_subscription_id")
        .eq("id", str(user.id))
        .single()
        .execute()
    )

    if not result.data:
        return {"plan": "free", "status": "inactive", "period_end": None, "provider": "creem"}

    return {
        "plan": result.data.get("plan") or "free",
        "status": result.data.get("subscription_status") or "inactive",
        "period_end": result.data.get("current_period_end"),
        "provider": "creem",
    }


@router.post("/portal")
async def create_portal(
    user=Depends(get_current_user),
    supabase=Depends(get_supabase_client),
):
    """Create a Creem customer portal link for managing subscriptions."""
    result = (
        supabase.table("profiles")
        .select("stripe_customer_id")
        .eq("id", str(user.id))
        .single()
        .execute()
    )

    customer_id = (result.data or {}).get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="No active Creem billing account yet. Upgrade to Pro first.",
        )

    try:
        portal = await creem.create_customer_portal(customer_id)
    except CreemError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    portal_url = portal.get("customer_portal_link") or portal.get("customerPortalLink")
    if not portal_url:
        raise HTTPException(status_code=502, detail="Creem did not return a portal URL.")

    return {"portal_url": portal_url, "provider": "creem"}


@router.post("/webhook")
async def creem_webhook(request: Request):
    """
    Handle Creem webhooks for subscription lifecycle events.
    Configure this URL in Creem Dashboard → Developers → Webhooks:
      https://YOUR_API_HOST/billing/webhook
    """
    payload = await request.body()
    signature = request.headers.get("creem-signature") or request.headers.get("Creem-Signature")

    # Allow local/dev bootstrapping without a secret; require it when configured.
    if settings.CREEM_WEBHOOK_SECRET:
        if not creem.verify_webhook_signature(payload, signature):
            raise HTTPException(status_code=401, detail="Invalid Creem webhook signature")

    try:
        event = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    event_type = event.get("eventType") or event.get("event_type") or event.get("type")
    obj = event.get("object") or {}
    supabase = get_supabase_client()

    if event_type == "checkout.completed":
        _handle_checkout_completed(supabase, obj)
    elif event_type in {"subscription.active", "subscription.paid", "subscription.update"}:
        _handle_subscription_active(supabase, obj, status="active")
    elif event_type == "subscription.trialing":
        _handle_subscription_active(supabase, obj, status="trialing")
    elif event_type == "subscription.scheduled_cancel":
        _handle_subscription_active(supabase, obj, status="scheduled_cancel")
    elif event_type in {"subscription.canceled", "subscription.expired", "subscription.paused"}:
        _handle_subscription_ended(supabase, obj, status=event_type.split(".")[-1])
    elif event_type == "subscription.past_due":
        _handle_subscription_ended(supabase, obj, status="past_due", downgrade=False)

    return {"status": "received"}


def _resolve_user_id(supabase, obj: dict) -> Optional[str]:
    metadata = obj.get("metadata") or {}
    if isinstance(obj.get("subscription"), dict):
        metadata = {**(obj["subscription"].get("metadata") or {}), **metadata}

    user_id = metadata.get("supabase_user_id") or metadata.get("user_id")
    if user_id:
        return str(user_id)

    # Fallback: request_id from checkout
    request_id = obj.get("request_id")
    if request_id:
        return str(request_id)

    customer_id = _extract_id(obj.get("customer"))
    if customer_id:
        result = (
            supabase.table("profiles")
            .select("id")
            .eq("stripe_customer_id", customer_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["id"]

    email = None
    customer = obj.get("customer")
    if isinstance(customer, dict):
        email = customer.get("email")
    if email:
        result = (
            supabase.table("profiles")
            .select("id")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["id"]

    return None


def _handle_checkout_completed(supabase, checkout: dict):
    user_id = _resolve_user_id(supabase, checkout)
    if not user_id:
        print("Creem webhook checkout.completed: could not resolve user")
        return

    customer_id = _extract_id(checkout.get("customer"))
    subscription = checkout.get("subscription") if isinstance(checkout.get("subscription"), dict) else {}
    subscription_id = _extract_id(subscription) or _extract_id(checkout.get("subscription"))
    product = checkout.get("product") if isinstance(checkout.get("product"), dict) else {}
    product_id = _extract_id(product)
    if not product_id and isinstance(checkout.get("order"), dict):
        product_id = _extract_id(checkout["order"].get("product"))

    plan = (checkout.get("metadata") or {}).get("plan") or creem.plan_for_product_id(product_id)

    update: dict[str, Any] = {
        "plan": plan,
        "subscription_status": "active",
        "current_period_end": _period_end(subscription),
    }
    if customer_id:
        update["stripe_customer_id"] = customer_id  # stores Creem customer id
    if subscription_id:
        update["stripe_subscription_id"] = subscription_id  # stores Creem subscription id

    supabase.table("profiles").update(update).eq("id", user_id).execute()


def _handle_subscription_active(supabase, subscription: dict, status: str = "active"):
    user_id = _resolve_user_id(supabase, subscription)
    customer_id = _extract_id(subscription.get("customer"))
    subscription_id = _extract_id(subscription) or subscription.get("id")
    product_id = _extract_id(subscription.get("product"))
    plan = (subscription.get("metadata") or {}).get("plan") or creem.plan_for_product_id(product_id)

    if not user_id and customer_id:
        result = (
            supabase.table("profiles")
            .select("id")
            .eq("stripe_customer_id", customer_id)
            .limit(1)
            .execute()
        )
        if result.data:
            user_id = result.data[0]["id"]

    if not user_id:
        print(f"Creem webhook subscription ({status}): could not resolve user")
        return

    update: dict[str, Any] = {
        "plan": plan,
        "subscription_status": status,
        "current_period_end": _period_end(subscription),
    }
    if customer_id:
        update["stripe_customer_id"] = customer_id
    if subscription_id:
        update["stripe_subscription_id"] = subscription_id

    update = {k: v for k, v in update.items() if v is not None}
    supabase.table("profiles").update(update).eq("id", user_id).execute()


def _handle_subscription_ended(supabase, subscription: dict, status: str, downgrade: bool = True):
    user_id = _resolve_user_id(supabase, subscription)
    customer_id = _extract_id(subscription.get("customer"))

    if not user_id and customer_id:
        result = (
            supabase.table("profiles")
            .select("id")
            .eq("stripe_customer_id", customer_id)
            .limit(1)
            .execute()
        )
        if result.data:
            user_id = result.data[0]["id"]

    if not user_id:
        print(f"Creem webhook subscription ended ({status}): could not resolve user")
        return

    update: dict[str, Any] = {
        "subscription_status": status,
        "current_period_end": _period_end(subscription),
    }
    if downgrade:
        update["plan"] = "free"
        if status in {"canceled", "expired"}:
            update["stripe_subscription_id"] = None

    supabase.table("profiles").update(update).eq("id", user_id).execute()
