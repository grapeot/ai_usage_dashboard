"""Capture metadata without turning presentation formatting into source data."""
from __future__ import annotations

from datetime import datetime
import base64
import json
from decimal import Decimal
from hashlib import sha256
from typing import Any

CAPTURE_FIELDS = (
    'observed_at', 'measurement_source', 'percentage_resolution',
    'pool_id', 'account_fingerprint', 'model_quotas', 'quota_scope', 'absolute_limit_usd',
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def percentage_resolution(value: str | int | float) -> float:
    return float(Decimal(10) ** Decimal(str(value)).as_tuple().exponent)


def fraction_used(remaining: int | float) -> float:
    return float((Decimal(1) - Decimal(str(remaining))) * 100)


def ratio_percentage(value: int | float) -> float:
    return float(Decimal(str(value)) * 100)


def account_fingerprint(account_id: str | None) -> str | None:
    return sha256(account_id.encode()).hexdigest()[:16] if account_id else None


def openai_account_fingerprint(auth: dict[str, Any]) -> str | None:
    tokens = auth.get('tokens') or {}
    if not isinstance(tokens, dict):
        tokens = {}
    account = auth.get('accountId') or tokens.get('account_id')
    if not account:
        token = auth.get('access') or tokens.get('access_token') or ''
        try:
            encoded = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            context = claims.get('https://api.openai.com/auth') if isinstance(claims, dict) else None
            account = context.get('chatgpt_account_id') if isinstance(context, dict) else None
        except (ValueError, TypeError, IndexError):
            return None
    return account_fingerprint(f'openai:{account}') if isinstance(account, str) and account else None


def capture(
    quotas: list[dict[str, Any]], *, observed_at: str | None = None,
    source: str = 'live', resolution: float | None = None,
    account: str | None = None,
) -> list[dict[str, Any]]:
    timestamp = observed_at or now_iso()
    result = []
    for quota in quotas:
        item = dict(quota)
        item.setdefault('observed_at', timestamp)
        item.setdefault('measurement_source', source)
        item.setdefault('percentage_resolution', resolution)
        item.setdefault('account_fingerprint', account)
        item.setdefault('pool_id', f"{item.get('provider', 'unknown')}:{item.get('label', 'unknown')}")
        result.append(item)
    return result
