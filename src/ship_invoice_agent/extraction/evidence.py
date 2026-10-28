"""Build trustworthy line references and validate model claims against their sources."""

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument, RawDocument

DOCUMENT_TYPES = {
    "INVOICE": "Invoice",
    "DEBIT_NOTE": "Debit Note",
    "CREDIT_NOTE": "Credit Note",
    "ARRIVAL_NOTICE": "Arrival Notice",
    "PRE_ALERT": "Pre-Alert",
    "BILL_OF_LADING": "Bill of Lading",
    "OTHER": "Other",
}
_TYPE_PATTERNS = {
    "INVOICE": r"invoice",
    "DEBIT_NOTE": r"debit\s*note|debit\s*memo",
    "CREDIT_NOTE": r"credit\s*note|credit\s*memo",
    "ARRIVAL_NOTICE": r"arrival\s*notice",
    "PRE_ALERT": r"pre[\s-]*alert",
    "BILL_OF_LADING": r"bill\s*of\s*lading|\bb/l\b",
}


def build_catalog(raw: RawDocument) -> dict[str, dict]:
    identity = f"{raw.file_hash}|{raw.parent_source}|{raw.source_name}"
    prefix = hashlib.sha256(identity.encode()).hexdigest()[:16]
    page = sheet = table = None
    catalog = {}
    for index, text in enumerate(raw.content.splitlines(), start=1):
        if match := re.fullmatch(r"\[PAGE (\d+)\]", text.strip()):
            page = int(match[1])
            continue
        if match := re.fullmatch(r"\[SHEET: (.*)\]", text.strip()):
            sheet = match[1]
            continue
        if match := re.fullmatch(r"\[TABLE (\d+)\]", text.strip()):
            table = int(match[1])
            continue
        if not text.strip():
            continue
        line_id = f"{prefix}:L{index:06d}"
        catalog[line_id] = {
            "line_id": line_id,
            "text": text,
            "text_line": index,
            "page": page,
            "sheet": sheet,
            "table": table,
            "source_file": raw.source_name,
            "file_hash": raw.file_hash,
        }
    return catalog


def catalog_content(catalog: dict[str, dict]) -> str:
    return "\n".join(f"[{key}] {item['text']}" for key, item in catalog.items())


def _facts(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in {"evidence", "observations", "change_requests"}:
                yield from _facts(child, f"{prefix}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _facts(child, f"{prefix}/{index}")
    elif value is not None:
        yield prefix, value


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _number_supported(value, source: str) -> bool:
    if not Decimal(str(value)).is_finite():
        return False
    for token in re.findall(
        r"(?<![A-Za-z0-9.])\(?[+-]?\d[\d,]*(?:\.\d+)?\)?(?![A-Za-z0-9.])", source
    ):
        try:
            number = Decimal(token.strip("()").replace(",", ""))
            if token.startswith("(") and token.endswith(")"):
                number = -number
            if number == Decimal(str(value)):
                return True
        except InvalidOperation:
            continue
    return False


def supports(path: str, value, source: str) -> bool:
    field = path.rsplit("/", 1)[-1]
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        return _number_supported(value, source)
    if field == "document_type":
        return value == "OTHER" or bool(re.search(_TYPE_PATTERNS.get(value, r"(?!)"), source, re.I))
    if field == "currency":
        aliases = {"CNY": r"\bRMB\b|CN¥", "USD": r"US\$", "EUR": "€"}
        return bool(re.search(rf"\b{re.escape(value)}\b", source, re.I)) or bool(
            re.search(aliases.get(value, r"(?!)"), source, re.I)
        )
    if field == "payment_term":
        pattern = r"\bPP\b|\bprepaid\b" if value == "PP" else r"\bCC\b|\bcollect\b"
        return bool(re.search(pattern, source, re.I))
    if field in {"document_date", "eta", "etd"}:
        expected = date.fromisoformat(value)
        for y, m, d in re.findall(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", source):
            if (int(y), int(m), int(d)) == (expected.year, expected.month, expected.day):
                return True
        for a, b, y in re.findall(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", source):
            a, b, y = int(a), int(b), int(y)
            if a > 12 and (y, b, a) == (expected.year, expected.month, expected.day):
                return True
            if b > 12 and (y, a, b) == (expected.year, expected.month, expected.day):
                return True
        return False
    if field.endswith("_no") or "/container_numbers/" in path:
        # No O/0, case, width, or punctuation correction at this boundary.
        return _compact(str(value)) in _compact(source)
    return _compact(str(value)).casefold() in _compact(source).casefold()


def adapt_documents(
    payload: dict, catalog: dict[str, dict], *, schema_version: str = "v1"
) -> list[ExtractedDocument]:
    documents = []
    for item in payload["documents"]:
        facts = dict(_facts(item))
        evidence = {}
        issues = []
        for entry in item["evidence"]:
            path = entry["field_path"]
            ids = entry["source_line_ids"]
            if path not in facts or any(key not in catalog for key in ids):
                issues.append(f"INVALID_EVIDENCE:{path}")
                continue
            evidence.setdefault(path, []).extend(ids)
        derived_type = False
        # Canonical document types are labels, rather than literal source strings.
        # If the model omitted this citation, only a verified reference-number
        # header may supply it; never search unrelated pages for a plausible type.
        if item["document_type"] and "/document_type" not in evidence:
            header_ids = evidence.get("/reference_no", [])
            for key in header_ids:
                source = catalog[key]["text"]
                kinds = {
                    kind
                    for kind, pattern in _TYPE_PATTERNS.items()
                    if re.search(pattern, source, re.I)
                }
                if (
                    kinds == {item["document_type"]}
                    and item["reference_no"]
                    and supports("/reference_no", item["reference_no"], source)
                ):
                    evidence["/document_type"] = [key]
                    derived_type = True
                    break
        for path, value in facts.items():
            ids = evidence.get(path, [])
            source = "\n".join(catalog[key]["text"] for key in ids)
            if not ids or not supports(path, value, source):
                issues.append(f"UNSUPPORTED_FACT:{path}")
        for observation in payload["observations"] + item["observations"]:
            issues.append(f"EXTRACTION_OBSERVATION:{observation['code']}")
            if any(key not in catalog for key in observation["source_line_ids"]):
                issues.append("INVALID_OBSERVATION_EVIDENCE")
        changes = {}
        for request in item["change_requests"]:
            path = request["field_path"]
            ids = request["source_line_ids"]
            match = re.fullmatch(r"/charges/(\d+)/(amount|payment_term)", path)
            source = "\n".join(catalog[key]["text"] for key in ids if key in catalog)
            target = facts.get(path)
            new_text = request["new_value_text"]
            change_words = r"更改|修改|调整|更新|改为|change|update|amend|revise|correct|set\b"
            target_name = (
                item["charges"][int(match[1])]["original_charge_item"]
                if match and int(match[1]) < len(item["charges"])
                else None
            )
            negated = re.search(
                r"(?:do not|don't|must not|should not)\s+(?:change|update|amend)"
                r"|不要修改|无需修改|保持不变",
                source,
                re.I,
            )
            field_supported = bool(
                match
                and (match[2] == "payment_term" or re.search(r"\bamount\b|金额", source, re.I))
            )
            valid = (
                request["action"] == "SET"
                and match is not None
                and target is not None
                and new_text is not None
                and all(key in catalog for key in ids)
                and _compact(new_text).casefold() in _compact(source).casefold()
                and supports(path, target, new_text)
                and re.search(change_words, source, re.I)
                and target_name
                and _compact(target_name).casefold() in _compact(source).casefold()
                and not negated
                and field_supported
            )
            if valid:
                changes.setdefault(int(match[1]), []).append(match[2])
            else:
                issues.append(f"UNVERIFIED_CHANGE_REQUEST:{path}")
        charges = []
        seen_rows = {}
        for index, charge in enumerate(item["charges"]):
            prefix = f"/charges/{index}/"
            refs = {p: ids for p, ids in evidence.items() if p.startswith(prefix)}
            ids = list(dict.fromkeys(key for values in refs.values() for key in values))
            positions = [catalog[key] for key in ids]
            name_ids = set(evidence.get(prefix + "original_charge_item", []))
            amount_ids = set(evidence.get(prefix + "amount", []))
            charge_issues = []
            if charge["amount"] is not None and not name_ids.intersection(amount_ids):
                charge_issues.append("CHARGE_ROW_ASSOCIATION_UNVERIFIED")
            charges.append(
                ExtractedCharge(
                    **charge,
                    confidence=None,
                    source_page=next((p["page"] for p in positions if p["page"]), None),
                    source_text="\n".join(p["text"] for p in positions) or None,
                    evidence=refs,
                    review_reasons=charge_issues,
                    explicit_update_fields=changes.get(index, []),
                )
            )
            row_key = (
                charge["original_charge_item"],
                charge["amount"],
                charge["currency"],
                tuple(sorted(name_ids)),
            )
            if name_ids and row_key in seen_rows:
                charges[index].review_reasons.append("DUPLICATE_EXTRACTED_ROW")
                charges[seen_rows[row_key]].review_reasons.append("DUPLICATE_EXTRACTED_ROW")
            seen_rows[row_key] = index
        values = {
            key: value for key, value in item.items() if key in ExtractedDocument.model_fields
        }
        values["document_type"] = DOCUMENT_TYPES.get(item["document_type"])
        values["charges"] = charges
        values["observations"] = payload["observations"] + item["observations"]
        values["evidence"] = [
            {
                "field_path": path,
                "sources": [catalog[key] for key in dict.fromkeys(ids)],
                "method": (
                    "verified_reference_header"
                    if path == "/document_type" and derived_type
                    else "model_reference"
                ),
            }
            for path, ids in evidence.items()
        ]
        if len(item["totals"]) == 1:
            for key in ("subtotal", "tax", "total"):
                values[key] = item["totals"][0][key]
        documents.append(
            ExtractedDocument(
                **values,
                extraction_schema_version=schema_version,
                review_reasons=sorted(set(issues)),
            )
        )
    return documents
