from typing import Literal

import simplejson

from ship_invoice_agent.contracts import validate_contract
from ship_invoice_agent.models import BillOperation
from ship_invoice_agent.output.schema import AssignmentBillOperation


def serialize_operations(
    operations: list[BillOperation],
    indent: int | None = 2,
    schema_mode: Literal["assignment", "internal"] = "assignment",
) -> str:
    if schema_mode == "assignment":
        payload = [
            AssignmentBillOperation.from_internal(operation).model_dump(mode="python")
            for operation in operations
        ]
        validate_contract(payload, "assignment-output")
    else:
        payload = [operation.model_dump(mode="python") for operation in operations]
    return simplejson.dumps(
        payload,
        ensure_ascii=False,
        indent=indent,
        allow_nan=False,
        use_decimal=True,
    )
