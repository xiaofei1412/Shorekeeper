from uuid import UUID, uuid1, uuid4

from fastapi.testclient import TestClient
import pytest

from shorekeeper.app import create_app
from shorekeeper.config import load_session_token
from shorekeeper.contracts import SpeechRequest


TOKEN = "a" * 43
VALID = {"text": "漂泊者，早上好。", "voice_profile_version": "not-connected"}


@pytest.fixture
def client():
    with TestClient(create_app(TOKEN)) as test_client:
        yield test_client


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert UUID(body["error"]["request_id"]).version == 4
    assert body["error"]["retryable"] is False
    assert TOKEN not in response.text


def test_health_is_process_only(client):
    assert client.get("/api/v1/health").json() == {"status": "ok", "scope": "process_only"}


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic " + TOKEN, "Bearer", "Bearer "])
@pytest.mark.parametrize("endpoint", ["/api/v1/status", "/api/v1/speech"])
def test_session_auth(client, authorization, endpoint):
    headers = {} if authorization is None else {"Authorization": authorization}
    if endpoint.endswith("speech"):
        response = client.post(endpoint, json=VALID, headers=headers)
    else:
        response = client.get(endpoint, headers=headers)
    assert_error(response, 401, "UNAUTHENTICATED")
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_precedes_json_validation(client):
    response = client.post("/api/v1/speech", content="{", headers={"Content-Type": "application/json"})
    assert_error(response, 401, "UNAUTHENTICATED")


def test_status_does_not_claim_product_readiness(client):
    response = client.get("/api/v1/status", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert response.json() == {
        "product_ready": False,
        "components": dict.fromkeys(["inference", "tts", "memory", "qq", "identity", "tools"], "not_configured"),
    }
    assert TOKEN not in response.text


@pytest.mark.parametrize("changes", [
    {"text": ""}, {"text": " \n\t\u3000"}, {"text": "字" * 1001},
    {"text": None}, {"text": 123}, {"text": ["hello"]},
    {"voice_profile_version": " \t"}, {"voice_profile_version": None}, {"voice_profile_version": 3},
    {"response_id": str(uuid1())}, {"segment_id": "not-a-uuid"}, {"cancel_group_id": 4},
    {"style": "happy"}, {"stream": True}, {"path": "E:/private/secret.wav"},
])
def test_invalid_speech_is_generic_422(client, changes):
    response = client.post("/api/v1/speech", json=VALID | changes, headers={"Authorization": "Bearer " + TOKEN})
    assert_error(response, 422, "INVALID_ARGUMENT")
    assert "E:/private" not in response.text
    assert "input" not in response.json()["error"]


@pytest.mark.parametrize("body", [{}, {"text": "hello"}, {"voice_profile_version": "v1"}, None, [], "hello"])
def test_missing_or_wrong_body(client, body):
    response = client.post("/api/v1/speech", json=body, headers={"Authorization": "Bearer " + TOKEN})
    assert_error(response, 422, "INVALID_ARGUMENT")


def test_authorized_invalid_json(client):
    response = client.post("/api/v1/speech", content="{", headers={
        "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json",
    })
    assert_error(response, 422, "INVALID_ARGUMENT")


@pytest.mark.parametrize("text", ["字", "字" * 1000, " \n字\t ", "😀" * 1000])
def test_valid_speech_is_unavailable(client, text):
    body = VALID | {"text": text, "response_id": str(uuid4()), "segment_id": None, "cancel_group_id": str(uuid4())}
    response = client.post("/api/v1/speech", json=body, headers={"Authorization": "bearer " + TOKEN})
    assert_error(response, 503, "DEPENDENCY_UNAVAILABLE")


def test_request_normalizes_uuid_and_whitespace():
    identifier = uuid4()
    body = SpeechRequest(**(VALID | {"text": " \n你好\t", "voice_profile_version": " v1 ", "response_id": identifier.hex.upper()}))
    assert body.text == "你好"
    assert body.voice_profile_version == "v1"
    assert str(body.response_id) == str(identifier)


def test_token_is_generated_and_reused(tmp_path):
    path = tmp_path / "runtime" / "session.token"
    first = load_session_token(path)
    assert len(first) == 43
    assert load_session_token(path) == first
    path.write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        load_session_token(path)
