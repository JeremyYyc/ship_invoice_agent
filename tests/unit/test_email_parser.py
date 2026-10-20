from pathlib import Path

from ship_invoice_agent.parsers.email import parse_email


def test_parses_email_body_headers_and_attachments():
    path = Path("tests/fixtures/documents/sample-pre-alert.eml")
    body, metadata, attachments = parse_email(path)

    assert "OOLU4055331281" in body
    assert "H/C $50" in body
    assert "FREIGHT PREPAID" in body
    assert "PRE-ALERT" in metadata["subject"]
    names = {name for name, _ in attachments}
    assert "4055331281\xa0MBL.pdf" in names
    assert "4055331281\xa0HBL.pdf" in names
