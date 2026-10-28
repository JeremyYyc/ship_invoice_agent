"""Build complete model-response fixtures with explicit references to test source lines."""

import re
from decimal import Decimal

import pytest

from ship_invoice_agent.contracts import extraction_schema
from ship_invoice_agent.extraction.evidence import DOCUMENT_TYPES, build_catalog


@pytest.fixture
def wire_response():
    def build(raw, expected):
        properties = extraction_schema("v1")["$defs"]["document"]["properties"]
        document = {
            key: [] if prop.get("type") == "array" else None for key, prop in properties.items()
        }
        catalog = build_catalog(raw)
        kind = {v: k for k, v in DOCUMENT_TYPES.items()}.get(expected.get("document_type"))
        document.update({key: value for key, value in expected.items() if key in properties})
        document["document_type"] = kind
        document["document_type_raw"] = expected.get("document_type")
        document["evidence"] = []
        document["observations"] = []
        document["change_requests"] = []
        document["charges"] = []

        def cite(path, value, line_ids=None):
            if value is None:
                return
            if line_ids is None:
                line_ids = [
                    key
                    for key, line in catalog.items()
                    if str(value).casefold() in line["text"].casefold()
                ]
            assert line_ids, (path, value, raw.content)
            document["evidence"].append({"field_path": path, "source_line_ids": line_ids})

        for field in properties:
            if field in {"evidence", "charges", "totals", "observations", "change_requests"}:
                continue
            value = document[field]
            if field == "document_type":
                cite("/document_type", expected.get("document_type"))
            elif isinstance(value, list):
                for index, entry in enumerate(value):
                    cite(f"/{field}/{index}", entry)
            else:
                cite(f"/{field}", value)
        used = set()
        for index, expected_charge in enumerate(expected.get("charges", [])):
            charge = {
                key: expected_charge.get(key)
                for key in (
                    "original_charge_item",
                    "quantity",
                    "unit_price",
                    "amount",
                    "currency",
                    "payment_term",
                )
            }
            charge["container_numbers"] = expected_charge.get("container_numbers", [])
            if charge["currency"]:
                charge["currency"] = charge["currency"].upper()
            name = charge["original_charge_item"]
            candidates = []
            for key, line in catalog.items():
                if key in used or name.casefold() not in line["text"].casefold():
                    continue
                numbers = [
                    Decimal(t.replace(",", ""))
                    for t in re.findall(r"\d[\d,]*(?:\.\d+)?", line["text"])
                ]
                if charge["amount"] is None or Decimal(str(charge["amount"])) in numbers:
                    candidates.append(key)
            assert candidates, name
            line_ids = candidates[:1]
            used.update(line_ids)
            for field, value in charge.items():
                if isinstance(value, list):
                    for entry_index, entry in enumerate(value):
                        cite(f"/charges/{index}/{field}/{entry_index}", entry)
                else:
                    refs = line_ids
                    if field in {"payment_term", "currency"} and value is not None:
                        if not any(value in catalog[key]["text"] for key in refs):
                            refs = None
                    cite(f"/charges/{index}/{field}", value, refs)
            document["charges"].append(charge)
        if expected.get("total") is not None:
            currency = expected["charges"][0]["currency"].upper()
            total = {
                "currency": currency,
                "subtotal": None,
                "tax": None,
                "discount": None,
                "total": expected["total"],
            }
            document["totals"] = [total]
            total_lines = [key for key, line in catalog.items() if "total" in line["text"].lower()]
            cite("/totals/0/total", total["total"], total_lines)
            cite("/totals/0/currency", currency)
        refs = {e["field_path"]: e["source_line_ids"] for e in document.pop("evidence")}

        def bind(value, path=""):
            if isinstance(value, dict):
                return {
                    key: child
                    if key in {"observations", "change_requests"}
                    else bind(child, f"{path}/{key}")
                    for key, child in value.items()
                }
            if isinstance(value, list):
                return [bind(child, f"{path}/{i}") for i, child in enumerate(value)]
            return {"value": value, "source_line_ids": refs.get(path, [])}

        return {"documents": [bind(document)], "observations": []}

    return build
