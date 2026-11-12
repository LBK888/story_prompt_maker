from fastapi.testclient import TestClient

from app.main import app


def test_home_and_openapi_render() -> None:
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "VID / AUTO PROMPT" in home.text
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/api/templates").status_code == 200

