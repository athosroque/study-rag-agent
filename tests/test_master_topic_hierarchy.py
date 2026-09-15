import pytest
from unittest.mock import patch, MagicMock
from src.models import ItemEstudo, StudyState, RelatorioReconciliacao, DecisaoIntegracao
from src.graph import (
    retrieve_similar_items_node,
    reconciliation_agent_node,
    db_writer_node
)
from src import db


def test_search_master_topic_filtering():
    """Testa que search_master_topic busca apenas registros mestres (parent_id IS NULL)."""
    with patch("src.db.get_db_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value = mock_cursor
        mock_conn.return_value.cursor.return_value = mock_cursor

        # Simula retorno do banco para o mestre
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "topico": "Atributos dos Atos Administrativos",
            "categoria": "teoria",
            "conteudo": "Presunção, Autoexecutoriedade...",
            "detalhes_resposta": None,
            "fragmentos": [],
            "criado_em": None,
            "data_ultima_revisao": None,
            "qtd_revisoes": 1,
            "link_do_video": None,
            "parent_id": None,
            "distance": 0.15
        }
        mock_cursor.fetchall.return_value = [
            {"id": 2, "topico": "Mnemônico PATI", "categoria": "sacada", "conteudo": "PATI", "detalhes_resposta": None, "criado_em": None, "link_do_video": None}
        ]

        res = db.search_master_topic(embedding=[0.1] * 384, min_similarity=0.70)
        assert res is not None
        assert res["id"] == 1
        assert res["similarity"] == 0.85
        assert len(res["filhos"]) == 1
        assert res["filhos"][0]["categoria"] == "sacada"


def test_retrieve_similar_items_node_anchors_master():
    """Testa que retrieve_similar_items_node ancora no Tópico Mestre existente."""
    state: StudyState = {
        "tema_busca": "Atributos do Ato Administrativo - Aprofundamento",
        "extracted_items": [
            {"topico": "Presunção de Legitimidade", "categoria": "teoria", "conteudo": "Conceito básico"}
        ]
    }

    mock_master = {
        "id": 1,
        "topico": "Atributos dos Atos Administrativos",
        "categoria": "teoria",
        "conteudo": "Conceito geral dos atos",
        "similarity": 0.88,
        "filhos": [],
        "fragmentos": []
    }

    with patch("src.embeddings.embedding_manager.embed_query", return_value=[0.1] * 384):
        with patch("src.db.search_master_topic", return_value=mock_master):
            with patch("src.db.search_similar_items", return_value=[]):
                res = retrieve_similar_items_node(state)
                assert res["master_topic_id"] == 1
                assert len(res["relevant_existing_items"]) >= 1
                assert res["relevant_existing_items"][0]["id"] == 1


def test_reconciliation_agent_discards_redundant_theory_and_adds_novelty():
    """Testa se o reconciliador descarta teoria redundante e anexa novidades quando master_topic_id existe."""
    item_teoria_redundante = ItemEstudo(
        topico="Atributos Básicos",
        categoria="teoria",
        conteudo="Os atributos são Presunção, Autoexecutoriedade, Tipicidade e Imperatividade."
    )
    item_nova_sacada = ItemEstudo(
        topico="Mnemônico Novo",
        categoria="sacada",
        conteudo="Nova dica prática de memorização."
    )
    item_nova_questao = ItemEstudo(
        topico="Questão Inédita",
        categoria="questao",
        conteudo="Questão sobre limites da imperatividade."
    )

    mock_decisions = [
        DecisaoIntegracao(
            item=item_teoria_redundante,
            acao="DESCARTAR",
            justificativa="Teoria já plenamente contemplada no Tópico Mestre ID 1."
        ),
        DecisaoIntegracao(
            item=item_nova_sacada,
            acao="ADICIONAR_FRAGMENTO",
            item_id_referencia=1,
            conteudo_incremental="Nova dica prática de memorização.",
            justificativa="Agregação de ferramenta mnemônica inédita ao Tópico Mestre ID 1."
        ),
        DecisaoIntegracao(
            item=item_nova_questao,
            acao="ADICIONAR_FRAGMENTO",
            item_id_referencia=1,
            conteudo_incremental="Questão sobre limites da imperatividade.",
            justificativa="Questão de fixação inédita vinculada ao Tópico Mestre ID 1."
        )
    ]

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.invoke.return_value = RelatorioReconciliacao(decisoes=mock_decisions)

    state: StudyState = {
        "tema_busca": "Atributos do Ato",
        "master_topic_id": 1,
        "extracted_items": [
            item_teoria_redundante.model_dump(),
            item_nova_sacada.model_dump(),
            item_nova_questao.model_dump()
        ],
        "relevant_existing_items": [{"id": 1, "topico": "Atributos", "filhos": [], "fragmentos": []}]
    }

    with patch("src.graph.get_llm", return_value=mock_llm):
        res = reconciliation_agent_node(state)
        decisions = res["reconciliation_decisions"]
        assert len(decisions) == 3
        assert decisions[0]["acao"] == "DESCARTAR"
        assert decisions[1]["acao"] == "ADICIONAR_FRAGMENTO"
        assert decisions[1]["item_id_referencia"] == 1
        assert decisions[2]["acao"] == "ADICIONAR_FRAGMENTO"
        assert decisions[2]["item_id_referencia"] == 1


def test_db_writer_node_no_new_master_when_reingesting():
    """Testa que durante a reingestão de aula com Tópico Mestre existente, 0 mestres são criados e itens são vinculados."""
    state: StudyState = {
        "tema_busca": "Atributos - Aprofundamento",
        "master_topic_id": 1,
        "video_encontrado": {"link": "https://youtube.com/watch?v=aula_nova"},
        "reconciliation_decisions": [
            {
                "item": {"topico": "Teoria Geral", "categoria": "teoria", "conteudo": "Teoria redundante"},
                "acao": "DESCARTAR",
                "justificativa": "Redundante"
            },
            {
                "item": {"topico": "Questão Nova", "categoria": "questao", "conteudo": "Nova questão", "detalhes_resposta": "Gabarito A"},
                "acao": "ADICIONAR_FRAGMENTO",
                "item_id_referencia": 1,
                "conteudo_incremental": "Nova questão",
                "justificativa": "Questão inédita"
            }
        ]
    }

    with patch("src.embeddings.embedding_manager.embed_query", return_value=[0.1] * 384):
        with patch("src.db.append_fragment_to_item", return_value=True) as mock_append:
            with patch("src.db.insert_new_item") as mock_insert:
                with patch("src.db.save_processed_video"):
                    res = db_writer_node(state)
                    stats = res["summary_stats"]
                    assert stats["criados"] == 0
                    assert stats["fragmentos"] == 1
                    assert stats["descartados"] == 1
                    mock_insert.assert_not_called()
                    mock_append.assert_called_once()
                    assert mock_append.call_args.kwargs["item_id"] == 1
                    assert mock_append.call_args.kwargs["categoria"] == "questao"
