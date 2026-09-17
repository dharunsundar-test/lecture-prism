from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import routes
from app.db.database import init_db
from app.services.worker import processing_worker
from app.core.config import settings


app = FastAPI(
    title="Lecture Capture Host",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)


@app.on_event("startup")
async def startup():
    init_db()
    await processing_worker.start()
    print(f"Server started on {settings.host}:{settings.port}")
    print(f"Data directory: {settings.data_dir}")


@app.on_event("shutdown")
async def shutdown():
    await processing_worker.stop()
    print("Server shutting down")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)