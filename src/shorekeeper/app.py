import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import secrets
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import UUID4

from shorekeeper.contracts import ApiError, ErrorResponse, SpeechRequest
from shorekeeper.voice.service import SpeechFailure, VoiceService


def create_app(session_token: str, tts_config: Path | None = None, *, service: VoiceService | None = None) -> FastAPI:
    tts = service if service is not None else VoiceService(tts_config)
    generations: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app):
        tts.start()
        try:
            yield
        finally:
            await tts.close()
            if generations:
                await asyncio.gather(*generations, return_exceptions=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.tts = tts

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
        if (request.url.path in {"/api/v1/status", "/api/v1/speech", "/api/v1/audio"}
                or request.url.path.startswith("/api/v1/audio/")):
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
                "tts": tts.state,
                "memory": "not_configured",
                "qq": "not_configured",
                "identity": "not_configured",
                "tools": "not_configured",
            },
        }

    @app.post("/api/v1/speech", responses={503: {"model": ErrorResponse}})
    async def speech(body: SpeechRequest, request: Request):
        # The generation keeps its slot on client disconnect; cancellation is a later slice.
        task = asyncio.create_task(tts.synthesize(body.text, body.voice_profile_version))
        generations.add(task)
        task.add_done_callback(generations.discard)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            asset_id, asset = await asyncio.shield(task)
        except SpeechFailure as failure:
            return error(request, failure.status, failure.code, "The speech request could not be completed.")
        return {
            "status": "succeeded", "audio_asset_id": str(asset_id), "format": "wav",
            "duration_s": asset.duration_s, "error": None,
        }

    @app.get("/api/v1/audio/{audio_asset_id}")
    async def audio(audio_asset_id: UUID4, request: Request):
        asset = tts.assets.get(audio_asset_id)
        if asset is None:
            return error(request, 404, "NOT_FOUND", "Audio is not available in this session.")
        return FileResponse(asset.path, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    return app
