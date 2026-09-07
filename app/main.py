from fastapi import FastAPI

app = FastAPI(
    title="ArriseAPI",
    version="0.1.0",
    description="API for receiving documents sent by Microsoft Power Automate.",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
