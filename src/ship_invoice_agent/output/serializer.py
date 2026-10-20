import json

from ship_invoice_agent.models import BillOperation


def serialize_operations(operations: list[BillOperation], indent: int | None = 2) -> str:
    return json.dumps(
        [operation.model_dump(mode="json") for operation in operations],
        ensure_ascii=False,
        indent=indent,
        allow_nan=False,
    )
