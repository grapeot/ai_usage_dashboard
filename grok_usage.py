"""Fetch SuperGrok / X Premium weekly usage pool from grok.com.

Uses the same grpc-web endpoint the grok.com Settings → Usage page calls:
POST /grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig

Auth is a browser cookie string in GROK_COOKIE (never commit real cookies).
"""
from __future__ import annotations

import struct
from datetime import datetime
from typing import Any


GROK_CREDITS_URL = 'https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig'
# Empty GetGrokCreditsConfigRequest inside a grpc-web data frame.
_GRPC_WEB_EMPTY_REQUEST = b'\x00\x00\x00\x00\x00'


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return value, i


def _parse_timestamp(data: bytes) -> int | None:
    """Return epoch seconds from a google.protobuf.Timestamp message."""
    i = 0
    seconds: int | None = None
    while i < len(data):
        key, i = _read_varint(data, i)
        field = key >> 3
        wire = key & 7
        if wire == 0:
            value, i = _read_varint(data, i)
            if field == 1:
                seconds = value
        elif wire == 2:
            length, i = _read_varint(data, i)
            i += length
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            break
    return seconds


def _parse_connect_frames(data: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    i = 0
    while i + 5 <= len(data):
        flags = data[i]
        length = struct.unpack('>I', data[i + 1 : i + 5])[0]
        payload = data[i + 5 : i + 5 + length]
        frames.append((flags, payload))
        i += 5 + length
    return frames


def parse_grok_credits_response(body: bytes) -> dict[str, Any]:
    """Parse grpc-web GetGrokCreditsConfigResponse into a plain dict.

    Observed GrokCreditsConfig field layout (from live responses + UI JS):
      1: credit_usage_percent (float32; omitted by proto3 when 0.0)
      4/5: period start/end timestamps (also mirrored under field 8)
      7: product_usage { product enum, usage_percent float; omitted at 0% }
      8: current_period { type enum, start, end }
      11: is_unified_billing_user
      12/13: newer optional fields (ignored)
    UsagePeriodType: 0 unspecified, 1 monthly, 2 weekly.
    Missing field 1 is treated as 0% used so the weekly bar still renders.
    """
    frames = _parse_connect_frames(body)
    if not frames:
        raise ValueError('empty grok credits response')

    message = frames[0][1]
    # GetGrokCreditsConfigResponse.config = field 1 (length-delimited)
    i = 0
    key, i = _read_varint(message, i)
    if (key >> 3) != 1 or (key & 7) != 2:
        raise ValueError('unexpected grok credits response shape')
    length, i = _read_varint(message, i)
    config = message[i : i + length]

    credit_usage_percent: float | None = None
    period_type: int | None = None
    period_start_s: int | None = None
    period_end_s: int | None = None
    product_usage: list[dict[str, Any]] = []

    i = 0
    while i < len(config):
        key, i = _read_varint(config, i)
        field = key >> 3
        wire = key & 7
        if wire == 0:
            value, i = _read_varint(config, i)
            if field == 11:
                pass  # is_unified_billing_user
        elif wire == 5:
            value = struct.unpack('<f', config[i : i + 4])[0]
            i += 4
            if field == 1:
                credit_usage_percent = float(value)
        elif wire == 2:
            length, i = _read_varint(config, i)
            data = config[i : i + length]
            i += length
            if field in (4, 5):
                ts = _parse_timestamp(data)
                if field == 4 and ts is not None:
                    period_start_s = period_start_s or ts
                if field == 5 and ts is not None:
                    period_end_s = period_end_s or ts
            elif field == 7:
                # ProductUsage
                j = 0
                product = None
                usage_percent = None
                while j < len(data):
                    k2, j = _read_varint(data, j)
                    f2 = k2 >> 3
                    w2 = k2 & 7
                    if w2 == 0:
                        v2, j = _read_varint(data, j)
                        if f2 == 1:
                            product = v2
                    elif w2 == 5:
                        v2 = struct.unpack('<f', data[j : j + 4])[0]
                        j += 4
                        if f2 == 2:
                            usage_percent = float(v2)
                    elif w2 == 2:
                        ln2, j = _read_varint(data, j)
                        j += ln2
                    else:
                        break
                if product is not None or usage_percent is not None:
                    product_usage.append(
                        {
                            'product': product,
                            'usage_percent': usage_percent or 0.0,
                        }
                    )
            elif field == 8:
                # UsagePeriod
                j = 0
                while j < len(data):
                    k2, j2 = _read_varint(data, j)
                    f2 = k2 >> 3
                    w2 = k2 & 7
                    if w2 == 0:
                        v2, j = _read_varint(data, j2)
                        if f2 == 1:
                            period_type = v2
                    elif w2 == 2:
                        ln2, j3 = _read_varint(data, j2)
                        d2 = data[j3 : j3 + ln2]
                        j = j3 + ln2
                        ts = _parse_timestamp(d2)
                        if f2 == 2 and ts is not None:
                            period_start_s = ts
                        if f2 == 3 and ts is not None:
                            period_end_s = ts
                    elif w2 == 5:
                        j = j2 + 4
                    elif w2 == 1:
                        j = j2 + 8
                    else:
                        break
        elif wire == 1:
            i += 8
        else:
            break

    # proto3 omits default float 0.0, so a 0% period has no field 1 / field 7.
    if credit_usage_percent is None:
        credit_usage_percent = 0.0

    used = max(0, min(100, int(round(credit_usage_percent))))
    period_label = {
        1: 'Monthly',
        2: 'Weekly',
    }.get(period_type or 2, 'Weekly')

    next_reset_time_ms = int(period_end_s * 1000) if period_end_s else None
    next_reset_iso = (
        datetime.fromtimestamp(period_end_s).isoformat(timespec='seconds')
        if period_end_s
        else None
    )

    return {
        'credit_usage_percent': credit_usage_percent,
        'used_percentage': used,
        'period_type': period_type,
        'period_label': period_label,
        'period_start_s': period_start_s,
        'period_end_s': period_end_s,
        'next_reset_time_ms': next_reset_time_ms,
        'next_reset_iso': next_reset_iso,
        'product_usage': product_usage,
    }


def fetch_grok_credits_config(cookie: str, timeout: float = 20.0) -> bytes:
    import urllib.error
    import urllib.request

    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:153.0) '
            'Gecko/20100101 Firefox/153.0'
        ),
        'Accept': '*/*',
        'Content-Type': 'application/grpc-web+proto',
        'Origin': 'https://grok.com',
        'Referer': 'https://grok.com/?_s=usage',
        'Cookie': cookie,
        'x-grpc-web': '1',
    }
    request = urllib.request.Request(
        GROK_CREDITS_URL,
        data=_GRPC_WEB_EMPTY_REQUEST,
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200]
        raise RuntimeError(f'grok credits HTTP {exc.code}: {detail!r}') from exc


def export_grok_quota(cookie: str) -> list[dict[str, Any]]:
    """Return unified QuotaSnapshot-shaped list for the weekly usage pool."""
    raw = fetch_grok_credits_config(cookie)
    parsed = parse_grok_credits_response(raw)
    snapshot: dict[str, Any] = {
        'provider': 'grok',
        'label': parsed['period_label'],
        'percentage': parsed['used_percentage'],
    }
    if parsed.get('next_reset_time_ms') is not None:
        snapshot['next_reset_time_ms'] = parsed['next_reset_time_ms']
    if parsed.get('next_reset_iso'):
        snapshot['next_reset_iso'] = parsed['next_reset_iso']
    return [snapshot]


def filter_quotas_for_eink(quotas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """E-ink shows Antigravity Gemini only; drop Antigravity Claude/GPT bars."""
    filtered: list[dict[str, Any]] = []
    for quota in quotas:
        provider = str(quota.get('provider', '')).lower()
        label = str(quota.get('label', '')).lower()
        if provider == 'antigravity' and 'gemini' not in label:
            continue
        filtered.append(quota)
    return filtered
