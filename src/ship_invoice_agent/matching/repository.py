import json
import os
import tempfile
from pathlib import Path

from ship_invoice_agent.models.bill import ExistingBill


class JsonBillRepository:
    def __init__(self, path: Path):
        self.path = path
        self.bills = self._load()

    def _load(self) -> list[ExistingBill]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [ExistingBill.model_validate(item) for item in data]

    def all(self) -> list[ExistingBill]:
        return list(self.bills)

    def upsert(self, bill: ExistingBill) -> None:
        for index, existing in enumerate(self.bills):
            if existing.bill_id == bill.bill_id:
                self.bills[index] = bill
                return
        self.bills.append(bill)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [bill.model_dump(mode="json") for bill in self.bills],
            ensure_ascii=False,
            indent=2,
        )
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name, dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temp_name, self.path)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
