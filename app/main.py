from fastapi import FastAPI

from app.models import HealthResponse


app = FastAPI(
    title="Bounded Agent Development Loop",
    version="0.1.0",
)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")