from __future__ import annotations

import uvicorn
import argparse
import os

from app.config import load_config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the persistent Vid Auto Prompt backend")
    parser.add_argument("--mode", choices=("interactive", "agent"))
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.mode:
        os.environ["VID_AUTO_PROMPT_MODE"] = args.mode
    config = load_config()
    uvicorn.run("app.main:app", host=config.host, port=args.port or config.port, reload=False)

