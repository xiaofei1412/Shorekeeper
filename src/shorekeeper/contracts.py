from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, UUID4


class ApiError(BaseModel):
    code: Literal["INVALID_ARGUMENT", "UNAUTHENTICATED", "DEPENDENCY_UNAVAILABLE"]
    message: str
    request_id: UUID4
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ApiError


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)
    ]
    voice_profile_version: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
    ]
    response_id: UUID4 | None = None
    segment_id: UUID4 | None = None
    cancel_group_id: UUID4 | None = None


class SpeechResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["succeeded", "failed", "cancelled"]
    audio_asset_id: UUID4 | None = None
    format: str | None = None
    duration_s: Annotated[float, Field(gt=0)] | None = None
    error: ApiError | None = None
