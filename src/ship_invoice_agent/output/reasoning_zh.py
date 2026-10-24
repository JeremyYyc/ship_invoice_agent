import re
from typing import Any

from ship_invoice_agent.models import AuditTrail

_REASON_TRANSLATIONS = {
    "one exact record found among multiple identity candidates": (
        "在多个标识匹配候选中找到一条完全一致的记录"
    ),
    "multiple historical bills match the same charge identity": (
        "多条历史账单匹配同一费用标识"
    ),
    "no historical bill matched the configured strategies": (
        "未找到符合已配置匹配策略的历史账单"
    ),
    "identity matched but strong fields conflict": "账单标识匹配，但关键字段存在冲突",
    "historical bill and extracted charge have the same material values": (
        "历史账单与提取费用的关键字段一致"
    ),
    "a unique historical bill matched and explicit fields changed": (
        "已匹配唯一历史账单，且明确字段发生变化"
    ),
    "duplicate charge already appeared in this batch": "同一费用已在当前批次中出现",
    "accounting entity is issuer/payee": "记账主体为出票方或收款方，判定为应收",
    "accounting entity is recipient": "记账主体为收件方，判定为应付",
    "ACCOUNTING_ENTITY_NOT_PROVIDED": "未提供记账主体",
    "ACCOUNTING_ENTITY_ROLE_UNRESOLVED": "无法确定记账主体在交易中的角色",
    "model extraction exhausted its retry/time budget": "模型抽取已耗尽重试次数或时间预算",
    "original charge evidence matched multiple canonical charge names": (
        "原始费用证据同时匹配多个标准费用名称"
    ),
    "model charge name matched multiple canonical charge names": (
        "模型费用名称同时匹配多个标准费用名称"
    ),
    "no configured charge alias matched; preserved extracted name": (
        "未匹配到已配置的费用别名，保留提取名称"
    ),
}

_WARNING_TRANSLATIONS = {
    "AR_AP_UNRESOLVED": "无法确定应收或应付方向",
    "LOW_EXTRACTION_CONFIDENCE": "模型抽取置信度低于自动接受阈值",
    "MODEL_CHARGE_NAME_CONFLICT": "模型费用名称与原始证据的确定性映射冲突",
    "MODEL_CHARGE_NAME_OVERRIDDEN": "已使用原始证据的确定性映射覆盖模型费用名称",
    "AMBIGUOUS_CHARGE_ALIAS": "费用别名同时匹配多个标准名称",
    "UNKNOWN_CHARGE_ALIAS": "费用名称未匹配到已配置的标准别名",
    "NO_CHARGES_EXTRACTED": "未提取到费用记录",
    "NO_VALID_CHARGE_RECORD": "未生成有效费用记录",
    "LLM_EXTRACTION_FAILED": "模型抽取失败",
    "LLM_TIME_BUDGET_EXHAUSTED": "模型抽取时间预算已耗尽",
    "DUPLICATE_IN_CURRENT_BATCH": "当前批次中存在重复费用",
}

_FIELD_TRANSLATIONS = {
    "amount": "金额",
    "currency": "币种",
    "payment_term": "付款方式",
    "issuer": "出票方",
    "recipient": "收件方",
    "charge_item": "费用名称",
}

_ALIAS_REASON_PATTERNS = (
    (
        re.compile(r"^original_charge_item alias mapped deterministically to (.+)$"),
        "根据原始费用名称中的别名，确定性映射为{value}",
    ),
    (
        re.compile(r"^source_text alias mapped deterministically to (.+)$"),
        "根据费用证据原文中的别名，确定性映射为{value}",
    ),
    (
        re.compile(r"^model charge name mapped to (.+)$"),
        "根据模型费用名称映射为{value}",
    ),
    (
        re.compile(
            r"^model proposed (.+); deterministic evidence took precedence$"
        ),
        "模型建议费用名称为{value}，最终以确定性原始证据为准",
    ),
)


def build_assignment_reasoning(audit_trail: AuditTrail) -> list[str]:
    """Render internal diagnostics as Chinese-only public audit explanations."""
    reasoning = [_translate_reason(item) for item in audit_trail.ai_reasoning]
    reasoning.extend(
        _format_change(field, values) for field, values in audit_trail.changed_fields.items()
    )
    reasoning.extend(_format_conflict(item) for item in audit_trail.conflicts)
    reasoning.extend(_translate_warning(warning) for warning in audit_trail.warnings)
    return list(dict.fromkeys(reasoning))


def _translate_reason(reason: str) -> str:
    if translated := _REASON_TRANSLATIONS.get(reason):
        return translated
    for pattern, template in _ALIAS_REASON_PATTERNS:
        if match := pattern.fullmatch(reason):
            return template.format(value=match.group(1))
    return "模型提示该记录存在需要人工核对的不确定信息"


def _translate_warning(warning: str) -> str:
    if translated := _WARNING_TRANSLATIONS.get(warning):
        return translated
    parts = warning.split(":")
    code = parts[0]
    if code == "POSSIBLE_OCR_CHARACTER_CONFUSION":
        return f"第{_one_based_index(parts)}条费用可能存在 OCR 字符混淆"
    if code == "NEGATIVE_AMOUNT":
        return f"第{_one_based_index(parts)}条费用的金额为负数"
    if code == "INVALID_CURRENCY":
        currency = parts[2] if len(parts) > 2 else "未知"
        return f"第{_one_based_index(parts)}条费用的币种 {currency} 无效"
    if code == "TOTAL_MISMATCH":
        calculated = parts[1] if len(parts) > 1 else "未知"
        stated = parts[2] if len(parts) > 2 else "未知"
        return f"费用合计 {calculated} 与单据总额 {stated} 不一致"
    return "模型提示该文档存在字段解释或 OCR 识别不确定性"


def _format_change(field: str, values: dict[str, Any]) -> str:
    label = _FIELD_TRANSLATIONS.get(field, field)
    return f"字段“{label}”由 {values.get('old')} 更新为 {values.get('new')}"


def _format_conflict(item: dict[str, Any]) -> str:
    field = str(item.get("field", "unknown"))
    label = _FIELD_TRANSLATIONS.get(field, field)
    return (
        f"字段“{label}”存在冲突：历史值为 {item.get('existing_value')}，"
        f"提取值为 {item.get('extracted_value')}"
    )


def _one_based_index(parts: list[str]) -> str:
    if len(parts) < 2:
        return "未知"
    try:
        return str(int(parts[1]) + 1)
    except ValueError:
        return parts[1]
