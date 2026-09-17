from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import pairing, routes
from app.db.database import init_db
from app.services.worker import processing_worker
from app.core.config import settings
from app.core.pairing import get_pair_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    get_pair_token()
    if settings.worker_enabled:
        await processing_worker.start()
    print(f"Server started on {settings.host}:{settings.port}")
    print(f"Data directory: {settings.data_dir}")
    print(f"Pair your phone: http://localhost:{settings.port}/api/pair")
    yield
    if settings.worker_enabled:
        await processing_worker.stop()
    print("Server shutting down")


app = FastAPI(
    title="Lecture Capture Host",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# The viewer reaches the API through the Vite dev proxy; the phone is not a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.health_router)
app.include_router(pairing.router)
app.include_router(routes.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
