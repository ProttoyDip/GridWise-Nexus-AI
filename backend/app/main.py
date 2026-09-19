"""FastAPI application entrypoint.

Wires together the API routers (health, optimize-energy) and exposes
the ASGI `app` instance used by the server (uvicorn) and by the Docker
image's start command.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import copilot, health, llm_status, optimize, optimize_stream, scheduler, system_status
from app.demo.routes import router as demo_router

app = FastAPI(title="GridWise Optimization API")

# CORS_ALLOW_ORIGINS: comma-separated list of allowed origins, e.g.
# "https://app.example.com,https://staging.example.com". Defaults to "*"
# (any origin) so local development and quick demos work out of the box;
# set it explicitly for any deployment a browser-based frontend will call.
_cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
_allow_origins = ["*"] if _cors_origins == "*" else [o.strip() for o in _cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_origins != ["*"],  # credentials + wildcard origin is invalid per the CORS spec
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(optimize.router)
app.include_router(optimize_stream.router)
app.include_router(llm_status.router)
app.include_router(system_status.router)
app.include_router(copilot.router)
app.include_router(scheduler.router)
app.include_router(demo_router)


@app.get("/")
def root() -> dict:
    """Trivial root route for platforms that health-check `/` by default;
    `GET /health` remains the canonical readiness probe."""
    return {"service": "GridWise Optimization API", "status": "ok", "docs": "/docs"}
