"""Entry point: ``python -m src.web``.

Boots the FastAPI app on ``http://127.0.0.1:8765``. Bound to localhost
by design -- this server processes uploaded videos with the local Python
pipeline and shouldn't be exposed to the network.
"""

from __future__ import annotations

import uvicorn

from src.web import create_app


def main() -> None:
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
