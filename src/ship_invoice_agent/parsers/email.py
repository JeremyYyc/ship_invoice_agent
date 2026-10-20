from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path


def parse_email(path: Path) -> tuple[str, dict[str, str | None], list[tuple[str, bytes]]]:
    message: EmailMessage = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    metadata = {
        "subject": str(message.get("Subject")) if message.get("Subject") else None,
        "from": str(message.get("From")) if message.get("From") else None,
        "to": str(message.get("To")) if message.get("To") else None,
        "cc": str(message.get("Cc")) if message.get("Cc") else None,
        "date": str(message.get("Date")) if message.get("Date") else None,
        "message_id": str(message.get("Message-ID")) if message.get("Message-ID") else None,
    }
    plain: list[str] = []
    html: list[str] = []
    attachments: list[tuple[str, bytes]] = []
    for part in message.walk():
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            attachments.append((filename, payload))
        elif part.get_content_type() == "text/plain":
            plain.append(part.get_content())
        elif part.get_content_type() == "text/html":
            html.append(part.get_content())
    body = "\n".join(plain).strip() or "\n".join(html).strip()
    header = "\n".join(f"{key}: {value}" for key, value in metadata.items() if value)
    return f"{header}\n\n{body}".strip(), metadata, attachments
