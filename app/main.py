from fastapi import FastAPI

from app.documents import router as documents_router

app = FastAPI(
    title="ArriseAPI",
    version="0.1.0",
    description="API for receiving documents sent by Microsoft Power Automate.",
)

app.include_router(documents_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
