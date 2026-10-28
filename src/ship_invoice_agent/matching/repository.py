import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import simplejson

from ship_invoice_agent.exceptions import RepositoryConflictError
from ship_invoice_agent.models.bill import ExistingBill


class JsonBillRepository:
    def __init__(self, path: Path, *, bills: list[ExistingBill] | None = None):
        self.path = path
        self.version: str | None = None
        self.bills = self._load() if bills is None else bills

    def fork(self) -> "JsonBillRepository":
        snapshot = JsonBillRepository(
            self.path, bills=[bill.model_copy(deep=True) for bill in self.bills]
        )
        snapshot.version = self.version
        return snapshot

    def _load(self) -> list[ExistingBill]:
        if not self.path.exists():
            return []
        content = self.path.read_bytes()
        self.version = hashlib.sha256(content).hexdigest()
        data = json.loads(content, parse_float=Decimal)
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
        payload = simplejson.dumps(
            [bill.model_dump(mode="python") for bill in self.bills],
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
            use_decimal=True,
        )
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RepositoryConflictError("another writer holds the bill repository lock") from exc
        temp_name = None
        try:
            current = (
                hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None
            )
            if current != self.version:
                raise RepositoryConflictError(
                    "historical bills changed; rebuild the operation plan"
                )
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name, dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
            self.version = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
            os.close(lock_fd)
            lock.unlink(missing_ok=True)
