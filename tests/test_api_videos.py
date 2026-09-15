from unittest.mock import patch
from fastapi.testclient import TestClient
from src.api import app

client = TestClient(app)


@patch("src.db.list_processed_videos")
def test_list_videos_endpoint(mock_list):
    mock_list.return_value = [
        {
            "link_do_video": "https://youtube.com/watch?v=aula_crase",
            "tema_busca": "Português - Crase",
            "titulo": "Aula Completa: Português - Crase",
            "tamanho_transcricao": 2500,
            "preview_transcricao": "Aula sobre Crase...",
            "data_processamento": "2026-09-12T15:00:00"
        }
    ]
    response = client.get("/api/v1/videos?limit=10&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["tema_busca"] == "Português - Crase"
    assert data[0]["tamanho_transcricao"] == 2500
    mock_list.assert_called_once_with(limit=10, offset=0)


@patch("src.db.get_processed_video")
def test_get_video_detail_success(mock_get):
    mock_get.return_value = {
        "link_do_video": "https://youtube.com/watch?v=aula_crase",
        "tema_busca": "Português - Crase",
        "titulo": "Aula Completa: Português - Crase",
        "transcricao_completa": "Transcrição integral da aula de crase com teoria, regras e exceções.",
        "data_processamento": "2026-09-12T15:00:00"
    }
    response = client.get("/api/v1/videos/detail?link=https://youtube.com/watch?v=aula_crase")
    assert response.status_code == 200
    data = response.json()
    assert data["link_do_video"] == "https://youtube.com/watch?v=aula_crase"
    assert "integral da aula de crase" in data["transcricao_completa"]
    mock_get.assert_called_once_with("https://youtube.com/watch?v=aula_crase")


@patch("src.db.get_processed_video")
def test_get_video_detail_not_found(mock_get):
    mock_get.return_value = None
    response = client.get("/api/v1/videos/detail?link=https://youtube.com/watch?v=inexistente")
    assert response.status_code == 404
