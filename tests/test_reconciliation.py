from unittest.mock import patch, MagicMock, ANY
from src.models import (
    ItemEstudo,
    ConjuntoItemsExtraidos,
    DecisaoIntegracao,
    RelatorioReconciliacao,
    StudyState
)
from src.graph import reconciliation_agent_node, db_writer_node


def test_reconciliation_empty_candidates():
    state: StudyState = {
        "extracted_items": [],
        "relevant_existing_items": []
    }
    res = reconciliation_agent_node(state)
    assert res["reconciliation_decisions"] == []


@patch("src.graph.get_llm")
def test_reconciliation_agent_decision_types(mock_get_llm):
    # Simula o LLM retornando as 3 decisões: CRIAR_NOVO, ADICIONAR_FRAGMENTO, DESCARTAR
    item1 = ItemEstudo(
        topico="Atributos",
        categoria="teoria",
        conteudo="A presunção de veracidade refere-se à verdade dos fatos.",
        detalhes_resposta=None
    )
    item2 = ItemEstudo(
        topico="Atributos",
        categoria="pegadinha",
        conteudo="Multas não têm autoexecutoriedade.",
        detalhes_resposta=None
    )
    item3 = ItemEstudo(
        topico="Atributos",
        categoria="teoria",
        conteudo="A autoexecutoriedade permite atuação direta.",
        detalhes_resposta=None
    )

    mock_chain = MagicMock()
    mock_chain.invoke.return_value = RelatorioReconciliacao(
        decisoes=[
            DecisaoIntegracao(
                item=item1,
                acao="CRIAR_NOVO",
                justificativa="Conceito novo sobre veracidade de fatos."
            ),
            DecisaoIntegracao(
                item=item2,
                acao="ADICIONAR_FRAGMENTO",
                item_id_referencia=10,
                conteudo_incremental="Multas não têm autoexecutoriedade",
                justificativa="Complementa o tópico de autoexecutoriedade existente no banco."
            ),
            DecisaoIntegracao(
                item=item3,
                acao="DESCARTAR",
                justificativa="Idêntico à teoria já presente no ID 10."
            )
        ]
    )

    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    mock_get_llm.return_value = mock_llm_instance

    state: StudyState = {
        "extracted_items": [item1.model_dump(), item2.model_dump(), item3.model_dump()],
        "relevant_existing_items": [{"id": 10, "topico": "Atributos", "conteudo": "Autoexecutoriedade..."}]
    }

    res = reconciliation_agent_node(state)
    decisions = res["reconciliation_decisions"]
    assert len(decisions) == 3
    assert decisions[0]["acao"] == "CRIAR_NOVO"
    assert decisions[1]["acao"] == "ADICIONAR_FRAGMENTO"
    assert decisions[1]["item_id_referencia"] == 10
    assert decisions[2]["acao"] == "DESCARTAR"


@patch("src.db.insert_new_item")
@patch("src.db.append_fragment_to_item")
@patch("src.db.save_processed_video")
@patch("src.embeddings.embedding_manager.embed_query")
def test_db_writer_node_upsert(mock_embed, mock_save_vid, mock_append, mock_insert):
    mock_embed.return_value = [0.1] * 384
    mock_insert.return_value = 99
    mock_append.return_value = True

    item_novo = ItemEstudo(topico="Tópico Novo", categoria="teoria", conteudo="Conteúdo inédito.")
    item_frag = ItemEstudo(topico="Tópico Existente", categoria="pegadinha", conteudo="Pegadinha nova.")

    state: StudyState = {
        "tema_busca": "Direito",
        "video_encontrado": {"link": "https://youtube.com/watch?v=aula_teste"},
        "reconciliation_decisions": [
            {
                "item": item_novo.model_dump(),
                "acao": "CRIAR_NOVO",
                "justificativa": "Novo conceito."
            },
            {
                "item": item_frag.model_dump(),
                "acao": "ADICIONAR_FRAGMENTO",
                "item_id_referencia": 15,
                "conteudo_incremental": "Pegadinha detalhada.",
                "justificativa": "Enriquece ID 15."
            },
            {
                "item": item_novo.model_dump(),
                "acao": "DESCARTAR",
                "justificativa": "Redundante."
            }
        ]
    }

    res = db_writer_node(state)
    stats = res["summary_stats"]
    assert stats["criados"] == 1
    assert stats["fragmentos"] == 1
    assert stats["descartados"] == 1
    assert mock_insert.call_count == 1
    # Primeira chamada: Mestre com parent_id=None
    assert mock_insert.call_args_list[0].kwargs["parent_id"] is None
    mock_append.assert_called_once_with(
        item_id=15,
        topico="Tópico Existente",
        categoria="pegadinha",
        conteudo_incremental="Pegadinha detalhada.",
        detalhes_resposta=None,
        justificativa="Enriquece ID 15.",
        link_do_video="https://youtube.com/watch?v=aula_teste",
        embedding=[0.1] * 384,
        materia_id=ANY
    )


def test_reconciliation_agent_contingency_with_master():
    """Testa a contingência do reconciliador quando já existe Tópico Mestre: teoria é descartada, sacadas agregadas."""
    state: StudyState = {
        "tema_busca": "Direito Administrativo",
        "master_topic_id": 1,
        "extracted_items": [
            {"topico": "Conceito de Ato", "categoria": "teoria", "conteudo": "Ato administrativo básico."},
            {"topico": "Mnemônico PATI", "categoria": "sacada", "conteudo": "Presunção, Autoexecutoriedade, Tipicidade, Imperatividade."},
            {"topico": "Questão Multa", "categoria": "questao", "conteudo": "Multa pecuniária tem autoexecutoriedade?"}
        ],
        "relevant_existing_items": [{"id": 1, "topico": "Atributos dos Atos Administrativos"}]
    }

    with patch("src.graph.get_llm") as mock_get_llm:
        mock_get_llm.side_effect = Exception("LLM failure trigger contingency")
        res = reconciliation_agent_node(state)
        decisions = res["reconciliation_decisions"]
        assert len(decisions) == 3
        # Teoria repetitiva: DESCARTAR
        assert decisions[0]["acao"] == "DESCARTAR"
        # Sacada: ADICIONAR_FRAGMENTO referenciando o Mestre ID 1
        assert decisions[1]["acao"] == "ADICIONAR_FRAGMENTO"
        assert decisions[1]["item_id_referencia"] == 1
        # Questão: ADICIONAR_FRAGMENTO referenciando o Mestre ID 1
        assert decisions[2]["acao"] == "ADICIONAR_FRAGMENTO"
        assert decisions[2]["item_id_referencia"] == 1
