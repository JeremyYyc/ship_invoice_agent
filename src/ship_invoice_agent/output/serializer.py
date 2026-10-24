import json
from typing import Literal

from ship_invoice_agent.models import BillOperation
from ship_invoice_agent.output.schema import AssignmentBillOperation


def serialize_operations(
    operations: list[BillOperation],
    indent: int | None = 2,
    schema_mode: Literal["assignment", "internal"] = "assignment",
) -> str:
    if schema_mode == "assignment":
        payload = [
            AssignmentBillOperation.from_internal(operation).model_dump(mode="json")
            for operation in operations
        ]
    else:
        payload = [operation.model_dump(mode="json") for operation in operations]
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=indent,
        allow_nan=False,
    )
