from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ship_invoice_agent.config import Settings


def main() -> None:
    settings = Settings()
    root = Path(settings.local_file_server_root)
    root.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(
        (settings.local_file_server_host, settings.local_file_server_port), handler
    )
    print(f"Serving {root} at {settings.local_file_base_url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
