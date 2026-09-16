import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from src.config import settings
from src import db
from src import revisoes
from src.models import (
    ProcessVideoRequest,
    SearchQueryRequest,
    ItemEstudoCompleto,
    MateriaSummary,
    ProcessedVideoSummary,
    ProcessedVideoDetail,
    StudyState,
    RespostaRevisaoRequest,
    RespostaRevisaoLoteRequest
)
from src.graph import study_graph, generate_video_slug
from src.embeddings import embedding_manager
from src.llm import get_langfuse_callback, flush_langfuse, get_langsmith_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("study-rag-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando Study RAG API...")
    try:
        db.init_db()
        logger.info("Banco de dados pronto.")
    except Exception as e:
        logger.warning(f"Aviso de inicialização do banco: {e}. O banco pode subir em paralelo via docker.")
    
    if settings.LANGCHAIN_TRACING_V2:
        if settings.LANGCHAIN_API_KEY:
            logger.info(f"LangSmith Tracing ativado no projeto '{settings.LANGCHAIN_PROJECT}' ({settings.LANGCHAIN_ENDPOINT}).")
            get_langsmith_client()
        else:
            logger.warning("LANGCHAIN_TRACING_V2 está habilitado, mas LANGCHAIN_API_KEY não foi configurada no ambiente.")
    else:
        logger.info("LangSmith Tracing desativado (LANGCHAIN_TRACING_V2=false).")

    yield
    logger.info("Encerrando Study RAG API.")


app = FastAPI(
    title="Study RAG Agent API",
    description="LangGraph RAG com Reconciliação Contínua de Conhecimento e pgvector",
    version="1.0.0",
    lifespan=lifespan
)

# Static files & Templates
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)
if not os.path.exists(TEMPLATES_DIR):
    os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# =====================================================================
# ROTAS PRINCIPAIS
# =====================================================================

@app.get("/", response_class=HTMLResponse)
async def index_view(request: Request):
    """Página principal do Dashboard do Curador RAG."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def healthcheck():
    """Verifica a integridade da API, banco e configurações."""
    db_ok = False
    try:
        conn = db.get_db_connection()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "healthy" if db_ok else "degraded",
        "database_connected": db_ok,
        "litellm_url": settings.LITELLM_BASE_URL,
        "model_fast": settings.MODEL_FAST,
        "model_mid": settings.MODEL_MID,
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_dim": settings.EMBEDDING_DIM
    }


@app.get("/api/v1/stats")
async def get_stats():
    """Retorna estatísticas consolidadas para o Dashboard."""
    try:
        return db.get_stats()
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {e}")
        return {
            "total_itens": 0,
            "total_videos": 0,
            "total_fragmentos": 0,
            "total_materias": 0,
            "categorias": {}
        }


@app.get("/api/v1/materias", response_model=List[MateriaSummary])
async def list_materias_endpoint():
    """Lista todas as matérias cadastradas com contagem de tópicos mestres e itens."""
    try:
        return db.list_materias()
    except Exception as e:
        logger.error(f"Erro ao listar matérias: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/items", response_model=List[Dict[str, Any]])
async def list_items_endpoint(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    categoria: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    only_parents: bool = Query(True, description="Listar apenas tópicos mestres"),
    materia_id: Optional[int] = Query(None, description="Filtro opcional por ID da matéria")
):
    """Lista tópicos de estudo cadastrados com suporte a busca, categoria, matéria e filtro de tópicos mestres."""
    try:
        return db.list_items(
            limit=limit,
            offset=offset,
            categoria=categoria,
            search=search,
            only_parents=only_parents,
            materia_id=materia_id
        )
    except Exception as e:
        logger.error(f"Erro ao listar itens: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/items/{item_id}")
async def get_item_endpoint(item_id: int):
    """Retorna um item específico com todo o histórico de fragmentos."""
    item = db.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item de estudo não encontrado.")
    return item


@app.get("/api/v1/videos", response_model=List[ProcessedVideoSummary])
async def list_videos_endpoint(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Lista as aulas/vídeos processados com metadados e prévia da transcrição."""
    try:
        return db.list_processed_videos(limit=limit, offset=offset)
    except Exception as e:
        logger.error(f"Erro ao listar vídeos processados: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/videos/detail", response_model=ProcessedVideoDetail)
async def get_video_detail_endpoint(link: str = Query(..., description="Link do vídeo processado")):
    """Retorna metadados e transcrição completa de um vídeo processado."""
    video = db.get_processed_video(link)
    if not video:
        raise HTTPException(status_code=404, detail="Vídeo processado não encontrado.")
    return video


@app.post("/api/v1/search", response_model=List[Dict[str, Any]])
async def semantic_search_endpoint(req: SearchQueryRequest):
    """Realiza busca vetorial semântica direta no PostgreSQL com suporte a filtro por matéria."""
    try:
        vector = embedding_manager.embed_query(req.query)
        results = db.search_similar_items(
            embedding=vector,
            limit=req.limit,
            categoria=req.categoria,
            materia_id=req.materia_id
        )
        return results
    except Exception as e:
        logger.error(f"Erro na busca semântica: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/process")
async def process_video_endpoint(req: ProcessVideoRequest):
    """Processa uma aula através do grafo LangGraph de reconciliação de ponta a ponta."""
    initial_state: StudyState = {
        "tema_busca": req.tema_busca,
    }
    if req.materia:
        initial_state["materia"] = req.materia

    if req.video_link or req.transcricao:
        slug = generate_video_slug(req.tema_busca)
        initial_state["video_encontrado"] = {
            "link": req.video_link or f"https://youtube.com/watch?v=aula_{slug}",
            "titulo": req.video_titulo or f"Aula: {req.tema_busca}",
            "transcricao": req.transcricao or ""
        }

    callbacks = []
    lf_callback = get_langfuse_callback()
    if lf_callback:
        callbacks.append(lf_callback)
    config = {
        "callbacks": callbacks,
        "run_name": "study_rag_curation",
        "tags": ["study-rag-agent", req.tema_busca],
        "metadata": {"project": settings.LANGCHAIN_PROJECT, "tema": req.tema_busca}
    }

    try:
        final_state = study_graph.invoke(initial_state, config=config)
        cespe_questions = final_state.get("cespe_questions", [])
        return {
            "tema_busca": final_state.get("tema_busca"),
            "materia": final_state.get("materia"),
            "materia_id": final_state.get("materia_id"),
            "video_valido": final_state.get("video_valido"),
            "video_encontrado": final_state.get("video_encontrado"),
            "extracted_items_count": len(final_state.get("extracted_items", [])),
            "relevant_existing_items_count": len(final_state.get("relevant_existing_items", [])),
            "reconciliation_decisions": final_state.get("reconciliation_decisions", []),
            "summary_stats": final_state.get("summary_stats", {}),
            "db_status": final_state.get("db_status"),
            "cespe_questions_count": len(cespe_questions),
            "cespe_questions": cespe_questions,
            "revisoes_agendadas": final_state.get("revisoes_agendadas", {})
        }
    except Exception as e:
        logger.error(f"Erro no processamento do grafo: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        flush_langfuse()


@app.get("/api/v1/process/stream")
async def process_video_stream_endpoint(
    tema: str = Query(..., description="Tema ou assunto específico da aula"),
    materia: Optional[str] = Query(None, description="Matéria ou disciplina geral da aula (opcional)"),
    link: Optional[str] = Query(None, description="Link do vídeo"),
    titulo: Optional[str] = Query(None, description="Título da aula"),
    transcricao: Optional[str] = Query(None, description="Transcrição completa (opcional)")
):
    """
    Streaming SSE da execução do LangGraph nó a nó em tempo real.
    Permite visualizar no Dashboard o andamento detalhado e as decisões de reconciliação.
    Desacoplado em worker thread assíncrono para liberar o event loop.
    """
    initial_state: StudyState = {"tema_busca": tema}
    if materia:
        initial_state["materia"] = materia

    if link or transcricao:
        slug = generate_video_slug(tema)
        initial_state["video_encontrado"] = {
            "link": link or f"https://youtube.com/watch?v=aula_{slug}",
            "titulo": titulo or f"Aula: {tema}",
            "transcricao": transcricao or ""
        }

    callbacks = []
    lf_callback = get_langfuse_callback()
    if lf_callback:
        callbacks.append(lf_callback)
    config = {
        "callbacks": callbacks,
        "run_name": "study_rag_stream",
        "tags": ["study-rag-agent", tema, "stream"],
        "metadata": {"project": settings.LANGCHAIN_PROJECT, "tema": tema, "mode": "stream"}
    }

    async def event_generator():
        yield {
            "event": "start",
            "data": json.dumps({"message": f"Iniciando curadoria RAG para '{tema}'...", "tema": tema})
        }

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def stream_worker():
            try:
                for step in study_graph.stream(initial_state, config=config):
                    loop.call_soon_threadsafe(queue.put_nowait, ("step", step))
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
            except Exception as err:
                logger.error(f"Erro no streaming do grafo worker: {err}")
                loop.call_soon_threadsafe(queue.put_nowait, ("error", err))
            finally:
                flush_langfuse()

        worker_task = asyncio.create_task(asyncio.to_thread(stream_worker))

        try:
            while True:
                msg_type, payload = await queue.get()
                if msg_type == "step":
                    for node_name, node_output in payload.items():
                        data_to_send = {
                            "node": node_name,
                            "status": "completed",
                            "data": node_output
                        }
                        yield {
                            "event": "node_update",
                            "data": json.dumps(data_to_send, ensure_ascii=False)
                        }
                elif msg_type == "done":
                    yield {
                        "event": "complete",
                        "data": json.dumps({"message": "Curadoria e reconciliação concluídas com sucesso!"})
                    }
                    break
                elif msg_type == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": str(payload)})
                    }
                    break
        finally:
            await worker_task

    return EventSourceResponse(event_generator())


# =====================================================================
# REVISÃO ESPAÇADA — CURVA DO ESQUECIMENTO (Ebbinghaus)
# =====================================================================

@app.get("/api/v1/revisoes/stats")
async def revisoes_stats_endpoint():
    """Resumo do ciclo de revisões: devidas hoje, atrasadas, próximos 7 dias e cobertura."""
    try:
        return revisoes.estatisticas()
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas de revisão: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/revisoes/pendentes", response_model=List[Dict[str, Any]])
async def revisoes_pendentes_endpoint(
    dias: int = Query(0, ge=0, le=365, description="Janela em dias a partir de hoje (0 = só hoje + atrasadas)"),
    materia_id: Optional[int] = Query(None, description="Filtro por matéria"),
    categoria: Optional[str] = Query(None, description="Filtro por categoria (teoria|sacada|pegadinha|questao)"),
    limite: int = Query(50, ge=1, le=500)
):
    """Lista as revisões em aberto (pendentes/adiadas) que vencem na janela informada."""
    try:
        return revisoes.listar_pendentes(
            dias=dias, materia_id=materia_id, categoria=categoria, limite=limite
        )
    except Exception as e:
        logger.error(f"Erro ao listar revisões pendentes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/revisoes/plano")
async def revisoes_plano_endpoint(
    dias: int = Query(30, ge=1, le=365, description="Horizonte do plano em dias"),
    materia_id: Optional[int] = Query(None, description="Filtro por matéria")
):
    """Monta o plano de revisões agrupado por dia, incluindo o backlog atrasado."""
    try:
        return revisoes.plano_revisoes(dias=dias, materia_id=materia_id)
    except Exception as e:
        logger.error(f"Erro ao montar plano de revisões: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/revisoes/backfill")
async def revisoes_backfill_endpoint(
    categoria: Optional[str] = Query(None, description="Opcional: gerar só para uma categoria"),
    base_hoje: bool = Query(False, description="Se true, agenda a partir de hoje em vez da data do item")
):
    """Gera a primeira revisão para todos os itens que ainda não têm ciclo aberto."""
    categorias = [categoria] if categoria else None
    base = revisoes.agora_local() if base_hoje else None
    try:
        return revisoes.backfill_revisoes(categorias=categorias, base_date=base)
    except Exception as e:
        logger.error(f"Erro no backfill de revisões: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/revisoes/responder")
async def revisoes_responder_endpoint(req: RespostaRevisaoRequest):
    """Registra se uma revisão foi feita (e com que desempenho) e reprograma o ciclo."""
    try:
        return revisoes.registrar_resposta(
            feita=req.feita,
            desempenho=req.desempenho,
            revisao_id=req.revisao_id,
            item_id=req.item_id,
            observacao=req.observacao
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao registrar resposta de revisão: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/revisoes/responder-lote")
async def revisoes_responder_lote_endpoint(req: RespostaRevisaoLoteRequest):
    """Registra várias respostas de revisão de uma só vez."""
    try:
        return {"resultados": revisoes.registrar_respostas_lote([r.model_dump() for r in req.respostas])}
    except Exception as e:
        logger.error(f"Erro ao registrar respostas em lote: {e}")
        raise HTTPException(status_code=500, detail=str(e))
