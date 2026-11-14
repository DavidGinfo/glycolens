from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    uvicorn.run("glycolens.api.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
