"""Unpack already validated v2 facts for the existing evidence and business pipeline."""

from copy import deepcopy


def _unpack(value, path, evidence):
    if isinstance(value, dict):
        if "value" in value:
            if value["value"] is not None:
                evidence.append(
                    {"field_path": path, "source_line_ids": list(value["source_line_ids"])}
                )
            return value["value"]
        return {
            key: (
                deepcopy(child)
                if key in {"observations", "change_requests"}
                else _unpack(child, f"{path}/{key}", evidence)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_unpack(child, f"{path}/{index}", evidence) for index, child in enumerate(value)]
    return value


def flatten_cited_payload(payload: dict) -> dict:
    documents = []
    for document in payload["documents"]:
        evidence = []
        flat = _unpack(document, "", evidence)
        flat["evidence"] = evidence
        documents.append(flat)
    return {"documents": documents, "observations": deepcopy(payload["observations"])}
