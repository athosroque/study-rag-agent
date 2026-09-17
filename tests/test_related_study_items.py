import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from src.api import app
from src import db

client = TestClient(app)


@patch("src.db.get_related_study_items")
def test_get_related_study_items_endpoint(mock_get_related):
    mock_get_related.return_value = {
        "item_id": 27,
        "topico": "Análise de Questão 1 (Reescrita)",
        "materia_nome": "Língua Portuguesa",
        "teoria": {
            "id": 22,
            "topico": "Reescrita de Frases",
            "categoria": "teoria",
            "conteudo": "Teoria de reescrita.",
            "metodo": "vetorial",
            "sim": 0.8133
        },
        "sacada": {
            "id": 26,
            "topico": "Oração Coordenada vs. Subordinada Substantiva",
            "categoria": "sacada",
            "conteudo": "Dica de oração.",
            "metodo": "hierarquia_semantica",
            "sim": 0.7675
        },
        "pegadinha": {
            "id": 23,
            "topico": "Análise de Reescrita - Exemplo 2",
            "categoria": "pegadinha",
            "conteudo": "Pegadinha de reescrita.",
            "metodo": "vetorial",
            "sim": 0.8009
        }
    }

    resp = client.get("/api/v1/items/27/relacionados")
    assert resp.status_code == 200
    data = resp.json()
    assert data["item_id"] == 27
    assert data["teoria"]["id"] == 22
    assert data["sacada"]["id"] == 26
    assert data["pegadinha"]["id"] == 23


@patch("src.db.get_related_study_items")
def test_get_related_study_items_not_found(mock_get_related):
    mock_get_related.return_value = None
    resp = client.get("/api/v1/items/99999/relacionados")
    assert resp.status_code == 404


def test_get_related_study_items_db_query_logic():
    """Testa a lógica da consulta de itens relacionados com mock do cursor."""
    with patch("src.db.get_db_connection") as mock_conn:
        mock_cur = MagicMock()
        mock_cur.__enter__.return_value = mock_cur
        mock_conn.return_value.cursor.return_value = mock_cur

        # Simula retorno do target (item alvo 27)
        mock_cur.fetchone.side_effect = [
            {
                "id": 27,
                "topico": "Análise de Questão 1 (Reescrita)",
                "categoria": "questao",
                "conteudo": "Frase original...",
                "parent_id": 22,
                "materia_id": 1,
                "materia_nome": "Língua Portuguesa",
                "tem_embedding": True
            },
            # teoria
            {
                "id": 22,
                "topico": "Reescrita de Frases",
                "categoria": "teoria",
                "conteudo": "Há dois tipos de reescrita...",
                "gabarito": None,
                "detalhes_resposta": None,
                "parent_id": None,
                "materia_id": 1,
                "materia_nome": "Língua Portuguesa",
                "sim": 0.8133,
                "same_topic": 0,
                "same_hierarchy": 1
            },
            # sacada
            {
                "id": 26,
                "topico": "Oração Coordenada vs. Subordinada Substantiva",
                "categoria": "sacada",
                "conteudo": "A banca troca 'e' por 'que'...",
                "gabarito": None,
                "detalhes_resposta": None,
                "parent_id": 22,
                "materia_id": 1,
                "materia_nome": "Língua Portuguesa",
                "sim": 0.7675,
                "same_topic": 0,
                "same_hierarchy": 1
            },
            # pegadinha
            {
                "id": 23,
                "topico": "Análise de Reescrita - Exemplo 2",
                "categoria": "pegadinha",
                "conteudo": "Mudar quase todas para todas...",
                "gabarito": None,
                "detalhes_resposta": None,
                "parent_id": 22,
                "materia_id": 1,
                "materia_nome": "Língua Portuguesa",
                "sim": 0.8009,
                "same_topic": 0,
                "same_hierarchy": 1
            }
        ]

        result = db.get_related_study_items(27)
        assert result is not None
        assert result["item_id"] == 27
        assert result["teoria"]["id"] == 22
        assert result["teoria"]["metodo"] == "hierarquia_semantica"
        assert result["sacada"]["id"] == 26
        assert result["pegadinha"]["id"] == 23
