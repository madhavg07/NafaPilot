"""NafaPilot FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.router import api_router
from app.config import get_settings
from app.data import DatasetMissing, UnknownMerchant
from app.logging_config import configure_logging
from app.services.campaign_store import CampaignError
from app.services.llm import LLMError

settings = get_settings()
configure_logging(settings.secret_values)

app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Profit-aware merchant growth decision engine. "
        "Prototype using synthetic data only; not an official Paytm product."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(LLMError)
async def llm_error_handler(_: Request, err: LLMError) -> JSONResponse:
    """Any LLM failure becomes a structured error. Messages never contain the key."""
    return JSONResponse(
        status_code=err.http_status,
        content={"error_code": err.error_code, "message": err.message},
    )


@app.exception_handler(DatasetMissing)
async def dataset_missing_handler(_: Request, err: DatasetMissing) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"error_code": "dataset_missing", "message": str(err)}
    )


@app.exception_handler(UnknownMerchant)
async def unknown_merchant_handler(_: Request, err: UnknownMerchant) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error_code": "merchant_not_found", "message": f"No merchant '{err}'."},
    )


@app.exception_handler(CampaignError)
async def campaign_error_handler(_: Request, err: CampaignError) -> JSONResponse:
    content: dict[str, object] = {"error_code": err.error_code, "message": err.message}
    if err.details:
        content["details"] = err.details
    return JSONResponse(status_code=err.http_status, content=content)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Liveness check. Does not touch the database or the LLM."""
    return {
        "status": "ok",
        "service": "nafapilot-api",
        "version": __version__,
        "environment": settings.environment,
        "llm_provider": settings.llm_provider,
    }


app.include_router(api_router, prefix="/api/v1")
