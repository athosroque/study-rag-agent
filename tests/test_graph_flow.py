from unittest.mock import patch, MagicMock
from src.graph import (
    video_search_node,
    validate_video_node,
    route_after_validation,
    generate_video_slug,
    extract_youtube_video_id,
    fetch_youtube_transcript,
    generate_synthetic_lecture,
    db_writer_node,
    clean_transcript,
    chunk_transcript,
    deduplicate_extracted_items,
    extraction_agent_node,
    cespe_agent_node,
    revisao_scheduler_node
)
from src.models import (
    StudyState,
    ItemEstudo,
    ConjuntoItemsExtraidos,
    QuestaoCespe,
    ConjuntoQuestoesCespe
)


def test_generate_video_slug_normalization():
    slug1 = generate_video_slug("Princípios do Direito Administrativo: Legalidade & Moralidade!")
    assert slug1 == "principios_do_direito_administrativo_legalidade_moralidade"

    slug2 = generate_video_slug("Árvores Rubro-Negras (Estruturas de Dados)")
    assert slug2 == "arvores_rubro_negras_estruturas_de_dados"

    assert generate_video_slug("") == "aula_geral"


def test_extract_youtube_video_id():
    # URL padrão watch?v=
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # URL com parâmetros adicionais
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&feature=shared") == "dQw4w9WgXcQ"
    # URL curta youtu.be
    assert extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # URL shorts
    assert extract_youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # URL embed
    assert extract_youtube_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # URLs inválidas ou vazias
    assert extract_youtube_video_id("https://vimeo.com/12345678") is None
    assert extract_youtube_video_id("") is None
    assert extract_youtube_video_id(None) is None


@patch("youtube_transcript_api.YouTubeTranscriptApi")
def test_fetch_youtube_transcript_success(mock_api_cls):
    mock_instance = MagicMock()
    mock_instance.fetch.return_value = [
        MagicMock(text="Olá a todos,"),
        MagicMock(text="hoje estudaremos Direito Administrativo.")
    ]
    mock_api_cls.return_value = mock_instance
    if hasattr(mock_api_cls, "get_transcript"):
        delattr(mock_api_cls, "get_transcript")

    transcript = fetch_youtube_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert transcript == "Olá a todos, hoje estudaremos Direito Administrativo."


@patch("youtube_transcript_api.YouTubeTranscriptApi")
def test_fetch_youtube_transcript_failure(mock_api_cls):
    mock_instance = MagicMock()
    mock_instance.fetch.side_effect = Exception("No transcripts available")
    mock_api_cls.return_value = mock_instance
    if hasattr(mock_api_cls, "get_transcript"):
        delattr(mock_api_cls, "get_transcript")

    transcript = fetch_youtube_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert transcript is None


@patch("src.graph.get_llm")
def test_generate_synthetic_lecture(mock_get_llm):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Aula completa e aprofundada sobre Crase na Língua Portuguesa."
    mock_llm.invoke.return_value = mock_response

    # Prompt | llm chain mock
    with patch("langchain_core.prompts.ChatPromptTemplate.invoke") as mock_prompt_inv:
        mock_prompt_inv.return_value = "prompt formatado"
        with patch("langchain_core.runnables.RunnableSequence.invoke", return_value=mock_response):
            lecture = generate_synthetic_lecture("Crase")
            assert "Crase" in lecture or "Aula" in lecture


def test_video_search_node_deterministic_slug():
    state1: StudyState = {"tema_busca": "Princípios do Direito Administrativo"}
    state2: StudyState = {"tema_busca": "Princípios do Direito Administrativo"}
    with patch("src.graph.generate_synthetic_lecture", return_value="Aula gerada"):
        res1 = video_search_node(state1)
        res2 = video_search_node(state2)
    assert res1["video_encontrado"]["link"] == res2["video_encontrado"]["link"]
    assert "principios_do_direito_administrativo" in res1["video_encontrado"]["link"]


@patch("src.graph.generate_synthetic_lecture")
def test_video_search_node_default_fallback_synthetic(mock_synth):
    mock_synth.return_value = "Aula sintética estruturada sobre Árvores Rubro-Negras."
    state: StudyState = {"tema_busca": "Árvores Rubro-Negras"}
    res = video_search_node(state)
    assert "video_encontrado" in res
    video = res["video_encontrado"]
    assert "link" in video
    assert "arvores_rubro_negras" in video["link"]
    assert video["transcricao"] == "Aula sintética estruturada sobre Árvores Rubro-Negras."
    mock_synth.assert_called_once_with("Árvores Rubro-Negras")


@patch("src.graph.fetch_youtube_transcript")
def test_video_search_node_with_youtube_link(mock_fetch):
    mock_fetch.return_value = "Transcrição obtida via YouTube Transcript API."
    state: StudyState = {
        "tema_busca": "Concurso Público",
        "video_encontrado": {
            "link": "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "titulo": "Aula ao Vivo"
        }
    }
    res = video_search_node(state)
    assert res["video_encontrado"]["transcricao"] == "Transcrição obtida via YouTube Transcript API."
    mock_fetch.assert_called_once_with("https://youtube.com/watch?v=dQw4w9WgXcQ")


def test_video_search_node_provided():
    state: StudyState = {
        "tema_busca": "Tema Teste",
        "video_encontrado": {
            "link": "https://youtube.com/watch?v=custom123",
            "titulo": "Aula Personalizada",
            "transcricao": "Transcrição customizada existente."
        }
    }
    res = video_search_node(state)
    assert res["video_encontrado"]["link"] == "https://youtube.com/watch?v=custom123"
    assert res["video_encontrado"]["transcricao"] == "Transcrição customizada existente."


@patch("src.db.check_video_processed")
def test_validate_video_node_new(mock_check):
    mock_check.return_value = False
    state: StudyState = {
        "video_encontrado": {
            "link": "https://youtube.com/watch?v=video_novo",
            "transcricao": "Conteúdo inédito"
        }
    }
    res = validate_video_node(state)
    assert res["video_valido"] is True
    assert res["raw_text"] == "Conteúdo inédito"
    assert route_after_validation(res) == "extraction_agent"


@patch("src.db.check_video_processed")
def test_validate_video_node_duplicate(mock_check):
    mock_check.return_value = True
    state: StudyState = {
        "video_encontrado": {
            "link": "https://youtube.com/watch?v=video_duplicado",
            "transcricao": "Conteúdo repetido"
        }
    }
    res = validate_video_node(state)
    assert res["video_valido"] is False
    assert "já processado" in res["db_status"]
    from langgraph.graph import END
    assert route_after_validation(res) == END


@patch("src.db.save_processed_video")
def test_db_writer_node_saves_transcript(mock_save_vid):
    state: StudyState = {
        "tema_busca": "Direito Tributário",
        "video_encontrado": {
            "link": "https://youtube.com/watch?v=trib123",
            "titulo": "Princípio da Anterioridade",
            "transcricao": "Aula completa sobre anterioridade nonagesimal."
        },
        "reconciliation_decisions": []
    }
    res = db_writer_node(state)
    assert "db_status" in res
    mock_save_vid.assert_called_once_with(
        link="https://youtube.com/watch?v=trib123",
        tema="Direito Tributário",
        titulo="Princípio da Anterioridade",
        transcricao_completa="Aula completa sobre anterioridade nonagesimal."
    )


def test_clean_transcript_removes_noise_and_spurious_chars():
    raw = "เฮ [Música] [Música] [Aplausos] Olá a todos! ♪ Sejam bem-vindos ao curso de Português. [Risos] Hoje falaremos sobre Crase."
    cleaned = clean_transcript(raw)
    assert "Música" not in cleaned
    assert "Aplausos" not in cleaned
    assert "Risos" not in cleaned
    assert "♪" not in cleaned
    assert "เฮ" not in cleaned
    assert cleaned == "Olá a todos! Sejam bem-vindos ao curso de Português. Hoje falaremos sobre Crase."


def test_clean_transcript_preserves_clean_text():
    text = "Crase é a fusão da preposição 'a' com o artigo 'a'."
    assert clean_transcript(text) == text
    assert clean_transcript("") == ""
    assert clean_transcript(None) == ""
    assert clean_transcript("[Música] [Aplausos]") == ""


def test_chunk_transcript_short_text():
    short = "Aula curta sobre Crase com menos de 100 caracteres."
    chunks = chunk_transcript(short, chunk_size=500)
    assert len(chunks) == 1
    assert chunks[0] == short


def test_chunk_transcript_long_text_boundaries_and_overlap():
    s1 = "Primeira sentença importante sobre crase. " * 15
    s2 = "Segunda sentença com dicas e macetes fundamentais. " * 15
    s3 = "Terceira sentença com questões de concurso resolvidas. " * 15
    long_text = s1 + s2 + s3
    chunks = chunk_transcript(long_text, chunk_size=700, overlap=100)
    assert len(chunks) > 1
    assert "Primeira sentença" in chunks[0]
    assert "Terceira sentença" in chunks[-1]


def test_deduplicate_extracted_items():
    items = [
        {"topico": "Crase", "categoria": "teoria", "conteudo": "A crase ocorre antes de palavras femininas."},
        {"topico": "Crase", "categoria": "teoria", "conteudo": "A crase ocorre antes de palavras femininas."},
        {"topico": "Crase", "categoria": "sacada", "conteudo": "Troque por palavra masculina ao verificar."},
        {"topico": "Regência", "categoria": "teoria", "conteudo": "Regência verbal e nominal na sintaxe."}
    ]
    deduped = deduplicate_extracted_items(items)
    assert len(deduped) == 3
    assert deduped[0]["categoria"] == "teoria"
    assert deduped[1]["categoria"] == "sacada"
    assert deduped[2]["topico"] == "Regência"


def test_extraction_agent_node_empty_or_pure_noise():
    state: StudyState = {"raw_text": "เฮ [Música] [Música] [Aplausos]"}
    res = extraction_agent_node(state)
    assert res["extracted_items"] == []


def test_extraction_agent_node_success():
    state: StudyState = {
        "tema_busca": "Crase",
        "raw_text": "Aula de Crase na Língua Portuguesa com explicação teórica completa."
    }
    with patch("src.graph.get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_structured = MagicMock()

        mock_item = ItemEstudo(
            topico="Regra Geral da Crase",
            categoria="teoria",
            conteudo="Fusão da preposição 'a' com artigo 'a'."
        )
        mock_structured.invoke.return_value = ConjuntoItemsExtraidos(items=[mock_item])
        mock_llm.with_structured_output.return_value = mock_structured
        mock_get_llm.return_value = mock_llm

        res = extraction_agent_node(state)
        assert len(res["extracted_items"]) == 1
        assert res["extracted_items"][0]["topico"] == "Regra Geral da Crase"


def test_extraction_agent_node_chunk_error_resilience_no_raw_fallback():
    state: StudyState = {
        "tema_busca": "Crase",
        "raw_text": "Texto suficientemente longo para testar resiliência a falhas de extração no modelo."
    }
    with patch("src.graph.get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.invoke.side_effect = Exception("Context length limit exceeded")
        mock_llm.with_structured_output.return_value = mock_structured
        mock_get_llm.return_value = mock_llm

        res = extraction_agent_node(state)
        assert res["extracted_items"] == []


def test_cespe_agent_node_success():
    state: StudyState = {
        "master_topic_id": 10,
        "tema_busca": "Atos Administrativos",
        "materia": "Direito Administrativo",
        "materia_id": 2,
        "video_encontrado": {"link": "https://youtube.com/watch?v=aula123"}
    }
    with patch("src.graph.db.get_item_by_id") as mock_get_item, \
         patch("src.graph.get_llm") as mock_get_llm, \
         patch("src.graph.embedding_manager.embed_query", return_value=[0.1] * 384), \
         patch("src.graph.db.append_fragment_to_item") as mock_append:

        mock_get_item.return_value = {
            "id": 10,
            "topico": "Atos Administrativos",
            "conteudo": "Conceito e atributos do ato.",
            "materia_nome": "Direito Administrativo",
            "materia_id": 2,
            "fragmentos": [
                {"categoria": "pegadinha", "conteudo_incremental": "Multa não tem autoexecutoriedade"}
            ],
            "filhos": []
        }

        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_questao = QuestaoCespe(
            topico="Atributos",
            enunciado="A cobrança de multa administrativa independe de ação de execução fiscal por gozar do atributo da autoexecutoriedade.",
            gabarito="ERRADO",
            justificativa="A exigibilidade da multa não se confunde com autoexecutoriedade; a cobrança requer execução fiscal.",
            pegadinha_explicada="A banca confunde exigibilidade com autoexecutoriedade."
        )
        mock_structured.invoke.return_value = ConjuntoQuestoesCespe(questoes=[mock_questao])
        mock_llm.with_structured_output.return_value = mock_structured
        mock_get_llm.return_value = mock_llm

        res = cespe_agent_node(state)
        assert len(res["cespe_questions"]) == 1
        assert res["cespe_questions"][0]["gabarito"] == "ERRADO"
        assert mock_append.called
        call_kwargs = mock_append.call_args.kwargs
        assert call_kwargs["item_id"] == 10
        assert call_kwargs["categoria"] == "questao"
        assert call_kwargs["gabarito"] == "ERRADO"
        assert "Gabarito" in call_kwargs["detalhes_resposta"]


def test_revisao_scheduler_node_success():
    state: StudyState = {"tema_busca": "Atos"}
    with patch("src.graph.revisoes.backfill_revisoes") as mock_backfill:
        mock_backfill.return_value = {"revisoes_criadas": 4, "itens_sem_revisao": 4}
        res = revisao_scheduler_node(state)
        assert res["revisoes_agendadas"]["revisoes_criadas"] == 4
        assert mock_backfill.called
