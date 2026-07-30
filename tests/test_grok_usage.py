from __future__ import annotations

import struct

from grok_usage import (
    filter_quotas_for_eink,
    parse_grok_credits_response,
)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            break
    return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _len_field(field: int, data: bytes) -> bytes:
    return _key(field, 2) + _varint(len(data)) + data


def _float_field(field: int, value: float) -> bytes:
    return _key(field, 5) + struct.pack('<f', value)


def _timestamp(seconds: int) -> bytes:
    return _key(1, 0) + _varint(seconds)


def _envelope(message: bytes) -> bytes:
    return b'\x00' + struct.pack('>I', len(message)) + message + b'\x80' + struct.pack('>I', 15) + b'grpc-status:0\r\n'


def test_parse_grok_credits_response_weekly_pool():
    # GrokCreditsConfig: percent=12.5, weekly period ending at known epoch
    period = (
        _key(1, 0)
        + _varint(2)  # WEEKLY
        + _len_field(2, _timestamp(1_785_224_849))
        + _len_field(3, _timestamp(1_785_829_649))
    )
    product = _key(1, 0) + _varint(2) + _float_field(2, 12.5)
    config = (
        _float_field(1, 12.5)
        + _len_field(7, product)
        + _len_field(8, period)
    )
    response = _len_field(1, config)
    body = _envelope(response)

    parsed = parse_grok_credits_response(body)
    assert parsed['used_percentage'] == 12
    assert parsed['period_label'] == 'Weekly'
    assert parsed['next_reset_time_ms'] == 1_785_829_649 * 1000
    assert parsed['product_usage'][0]['product'] == 2
    assert parsed['product_usage'][0]['usage_percent'] == 12.5


def test_filter_quotas_for_eink_keeps_antigravity_gemini_only():
    quotas = [
        {'provider': 'glm', 'label': '5h', 'percentage': 1},
        {'provider': 'antigravity', 'label': 'Gemini 5h', 'percentage': 10},
        {'provider': 'antigravity', 'label': 'Claude 5h', 'percentage': 20},
        {'provider': 'antigravity', 'label': 'GPT 5h', 'percentage': 30},
        {'provider': 'grok', 'label': 'Weekly', 'percentage': 12},
    ]
    filtered = filter_quotas_for_eink(quotas)
    labels = [(q['provider'], q['label']) for q in filtered]
    assert labels == [
        ('glm', '5h'),
        ('antigravity', 'Gemini 5h'),
        ('grok', 'Weekly'),
    ]
