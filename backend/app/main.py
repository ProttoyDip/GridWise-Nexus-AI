"""FastAPI application entrypoint.

Wires together the API routers (health, optimize-energy) and exposes
the ASGI `app` instance used by the server (uvicorn) and by the Docker
image's start command.
"""

from fastapi import FastAPI

from app.api import health, llm_status, optimize

app = FastAPI(title="GridWise Optimization API")

app.include_router(health.router)
app.include_router(optimize.router)
app.include_router(llm_status.router)
