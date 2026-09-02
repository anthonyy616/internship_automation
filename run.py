"""
Internship Automation Bot v2 — Server Entrypoint.

Usage:
    python run.py              # Start the FastAPI server
    python run.py --port 8080  # Custom port
"""

import os
import sys

if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 60)
    print("INTERNSHIP AUTOMATION BOT v2")
    print("=" * 60)
    print(f"Server:    http://localhost:8000")
    print(f"API Docs:  http://localhost:8000/docs")
    print(f"Admin:     http://localhost:8000/admin")
    print(f"WebSocket: ws://localhost:8000/ws")
    print("=" * 60 + "\n")

    uvicorn.run(
        "backend.app:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=os.getenv("APP_ENV", "development") == "development",
        reload_dirs=["backend", "frontend"],
    )
