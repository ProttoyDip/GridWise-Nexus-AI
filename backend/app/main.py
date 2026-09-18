"""FastAPI application entrypoint.

Wires together the API routers (health, optimize-energy) and exposes
the ASGI `app` instance used by the server (uvicorn) and by the Docker
image's start command.
"""

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, llm_status, optimize

app = FastAPI(title="GridWise Optimization API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(optimize.router)
app.include_router(llm_status.router)

