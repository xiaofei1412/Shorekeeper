import secrets
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from shorekeeper.contracts import ApiError, ErrorResponse, SpeechRequest


def create_app(session_token: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def error(request: Request, status: int, code: str, message: str) -> JSONResponse:
        body = ErrorResponse(error=ApiError(
            code=code, message=message, request_id=request.state.request_id
        ))
        return JSONResponse(
            status_code=status,
            content=body.model_dump(mode="json"),
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )

    @app.middleware("http")
    async def session_auth(request: Request, call_next):
        request.state.request_id = uuid4()
        if request.url.path in {"/api/v1/status", "/api/v1/speech"}:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                token.encode("utf-8"), session_token.encode("utf-8")
            ):
                return error(request, 401, "UNAUTHENTICATED", "A valid local session token is required.")
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error(request, 422, "INVALID_ARGUMENT", "Invalid speech request.")

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "scope": "process_only"}

    @app.get("/api/v1/status")
    async def status():
        return {
            "product_ready": False,
            "components": {
                "inference": "not_configured",
                "tts": "not_configured",
                "memory": "not_configured",
                "qq": "not_configured",
                "identity": "not_configured",
                "tools": "not_configured",
            },
        }

    @app.post("/api/v1/speech", responses={503: {"model": ErrorResponse}})
    async def speech(body: SpeechRequest, request: Request):
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "TTS is not configured.")

    return app
