from unittest.mock import patch, MagicMock
import pytest
from src.models import ItemEstudo, ItemEstudoCompleto, RevisaoOut, QuestaoCespe, ConjuntoQuestoesCespe, StudyState
from src.graph import cespe_agent_node
from src.bot.main import parse_question_payload
from src import revisoes


def test_item_estudo_model_with_gabarito():
    """Valida que os modelos Pydantic aceitam e serializam o campo gabarito."""
    item = ItemEstudo(
        materia="Direito Constitucional",
        topico="Controle de Constitucionalidade",
        categoria="questao",
        conteudo="O controle concentrado de constitucionalidade é de competência originária do STF.",
        gabarito="CERTO",
        detalhes_resposta="Art. 102, I, 'a' da CF/88."
    )
    assert item.gabarito == "CERTO"
    d = item.model_dump()
    assert d["gabarito"] == "CERTO"

    # ItemEstudoCompleto
    completo = ItemEstudoCompleto(
        id=1,
        topico="Crase",
        categoria="questao",
        conteudo="Em 'Obedeceu às ordens', o acento é obrigatório?",
        gabarito="Sim, pois rege preposição e ordens exige artigo.",
        detalhes_resposta="Explicação detalhada",
        criado_em="2026-09-16T12:00:00",
        data_ultima_revisao="2026-09-16T12:00:00"
    )
    assert completo.gabarito.startswith("Sim")

    # RevisaoOut
    rev_out = RevisaoOut(
        revisao_id=10,
        item_id=1,
        topico="Crase",
        categoria="questao",
        etapa=0,
        intervalo_dias=1,
        status="pendente",
        conteudo="Pergunta de teste",
        gabarito="Gabarito de teste",
        detalhes_resposta="Detalhes de teste"
    )
    assert rev_out.gabarito == "Gabarito de teste"


def test_cespe_agent_populates_gabarito():
    """Valida que o cespe_agent_node passa o gabarito isolado para ItemEstudo e db.append_fragment_to_item."""
    state: StudyState = {
        "tema_busca": "Atos Administrativos",
        "materia": "Direito Administrativo",
        "materia_id": 1,
        "master_topic_id": 10
    }

    mock_questao = QuestaoCespe(
        topico="Autoexecutoriedade",
        enunciado="A cobrança de multa administrativa independe de ação de execução fiscal.",
        gabarito="ERRADO",
        justificativa="A exigibilidade da multa não se confunde com autoexecutoriedade.",
        pegadinha_explicada="A banca confunde os conceitos."
    )

    with patch("src.graph.db.get_item_by_id") as mock_get_item, \
         patch("src.graph.get_llm") as mock_get_llm, \
         patch("src.graph.embedding_manager.embed_query") as mock_embed, \
         patch("src.graph.db.append_fragment_to_item") as mock_append:

        mock_get_item.return_value = {
            "id": 10,
            "topico": "Atos Administrativos",
            "conteudo": "Conceito de ato",
            "materia_nome": "Direito Administrativo",
            "fragmentos": [],
            "filhos": []
        }
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.invoke.return_value = ConjuntoQuestoesCespe(questoes=[mock_questao])
        mock_llm.with_structured_output.return_value = mock_structured
        mock_get_llm.return_value = mock_llm
        mock_embed.return_value = [0.1] * 384

        res = cespe_agent_node(state)
        assert len(res["cespe_questions"]) == 1
        assert res["cespe_questions"][0]["gabarito"] == "ERRADO"

        assert mock_append.called
        call_kwargs = mock_append.call_args.kwargs
        assert call_kwargs["item_id"] == 10
        assert call_kwargs["categoria"] == "questao"
        assert call_kwargs["gabarito"] == "ERRADO"
        assert "Gabarito" in call_kwargs["detalhes_resposta"]


def test_parse_question_payload_logic():
    """Valida a separação de enunciado e resposta tanto para novas questões quanto legadas."""
    # Caso 1: Nova questão com gabarito e detalhes separados
    enunciado, resp = parse_question_payload(
        conteudo="A autoexecutoriedade aplica-se às multas?",
        gabarito="ERRADO",
        detalhes_resposta="Multas exigem execução judicial fiscal."
    )
    assert enunciado == "A autoexecutoriedade aplica-se às multas?"
    assert "ERRADO" in resp
    assert "Multas exigem execução judicial fiscal." in resp

    # Caso 2: Resposta direta em gabarito sem detalhes extras
    enunciado, resp = parse_question_payload(
        conteudo="Pergunta de teste?",
        gabarito="Resposta direta correta.",
        detalhes_resposta="Resposta direta correta."
    )
    assert enunciado == "Pergunta de teste?"
    assert resp == "Resposta direta correta."

    # Caso 3: Legado residual com 'Resposta:' embutida no conteudo
    enunciado, resp = parse_question_payload(
        conteudo="Pergunta: Enunciado antigo. Resposta: Gabarito antigo extraído.",
        gabarito=None,
        detalhes_resposta=None
    )
    assert enunciado == "Pergunta: Enunciado antigo."
    assert resp == "Gabarito antigo extraído."
