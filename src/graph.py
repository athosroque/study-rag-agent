import json
import logging
import re
import unicodedata
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, START, END
from langchain_core.prompts import ChatPromptTemplate
from src.config import settings
from src.models import (
    StudyState,
    ConjuntoItemsExtraidos,
    RelatorioReconciliacao,
    ItemEstudo,
    DecisaoIntegracao
)
from src.llm import get_llm
from src.embeddings import embedding_manager
from src import db

logger = logging.getLogger(__name__)


def generate_video_slug(tema: str) -> str:
    """Gera um slug normalizado e determinístico a partir do tema da aula."""
    if not tema:
        return "aula_geral"
    normalized = unicodedata.normalize('NFKD', tema).encode('ASCII', 'ignore').decode('utf-8')
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', normalized.strip().lower()).strip('_')
    return slug or "aula_geral"



def extract_youtube_video_id(url: str) -> Optional[str]:
    """Extrai o ID de 11 caracteres de URLs do YouTube (watch, short, embed, youtu.be)."""
    if not url:
        return None
    patterns = [
        r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([a-zA-Z0-9_-]{11})',
        r'[?&]v=([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


NOISE_TAGS_PATTERN = re.compile(
    r'[\[\(]\s*(?:m[úu]sica|music|aplausos?|applause|risos?|laughter|inaud[íi]vel|inintelig[íi]vel|vinheta|palmas|gargalhadas?|suspiros?|choro|ru[íi]do|barulho|sil[êe]ncio|silence)\s*[\]\)]',
    re.IGNORECASE
)
MUSICAL_CHARS_PATTERN = re.compile(r'[♪♫♬♩♭♮♯🎵🎶]+')
SPURIOUS_SCRIPTS_PATTERN = re.compile(r'[\u0E00-\u0E7F]+')


def clean_transcript(text: str) -> str:
    """
    Sanitiza transcrições de vídeos removendo tags de ruído do YouTube,
    caracteres musicais, alucinações de alfabetos não latinos e espaços espúrios.
    """
    if not text:
        return ""

    # 1. Remove tags de ruído entre colchetes ou parênteses
    cleaned = NOISE_TAGS_PATTERN.sub(" ", text)

    # 2. Remove símbolos musicais
    cleaned = MUSICAL_CHARS_PATTERN.sub(" ", cleaned)

    # 3. Remove caracteres de alfabetos alucinados
    cleaned = SPURIOUS_SCRIPTS_PATTERN.sub(" ", cleaned)

    # 4. Normaliza espaçamentos e quebras de linha
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'\n\s*\n+', '\n\n', cleaned)
    return cleaned.strip()


def chunk_transcript(text: str, chunk_size: int = 10000, overlap: int = 800) -> List[str]:
    """
    Divide uma transcrição longa em blocos pedagógicos gerenciáveis,
    respeitando limites de frases e aplicando sobreposição (overlap).
    """
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    start = 0
    total_len = len(text)

    while start < total_len:
        end = start + chunk_size
        if end >= total_len:
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        # Tenta quebrar em limites de frase na janela final
        search_start = max(start, end - 1000)
        split_pos = -1

        for punct in [".\n", "!\n", "?\n", ". ", "! ", "? ", "\n\n", "; ", ", "]:
            pos = text.rfind(punct, search_start, end)
            if pos != -1:
                split_pos = pos + len(punct)
                break

        if split_pos == -1:
            pos = text.rfind(" ", search_start, end)
            if pos != -1:
                split_pos = pos + 1
            else:
                split_pos = end

        chunk = text[start:split_pos].strip()
        if chunk:
            chunks.append(chunk)

        prev_start = start
        start = max(start + 1, split_pos - overlap)
        next_space = text.find(" ", start, min(total_len, start + 150))
        if next_space != -1 and next_space < split_pos:
            start = next_space + 1

        if start <= prev_start:
            start = split_pos

    return chunks


def deduplicate_extracted_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplica itens extraídos com base em categoria, similaridade de tópico e conteúdo."""
    seen_keys = set()
    unique_items = []

    for item in items:
        cat = (item.get("categoria") or "").strip().lower()
        topico = (item.get("topico") or "").strip().lower()
        conteudo = (item.get("conteudo") or "").strip().lower()

        norm_topico = re.sub(r'[^a-zA-Z0-9]+', ' ', unicodedata.normalize('NFKD', topico)).strip()
        norm_prefix = re.sub(r'[^a-zA-Z0-9]+', ' ', unicodedata.normalize('NFKD', conteudo[:80])).strip()
        key = f"{cat}:{norm_topico}:{norm_prefix}"

        if key not in seen_keys:
            seen_keys.add(key)
            unique_items.append(item)
        else:
            logger.info(f"[deduplicate_extracted_items] Item redundante descartado na extração: '{item.get('topico')}' ({cat})")

    return unique_items


def fetch_youtube_transcript(url: str) -> Optional[str]:
    """Tenta extrair legendas de um vídeo do YouTube via youtube-transcript-api e sanitiza o texto."""
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        languages = ['pt', 'pt-BR', 'en']
        if hasattr(YouTubeTranscriptApi, 'get_transcript'):
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        else:
            api = YouTubeTranscriptApi()
            transcript_list = api.fetch(video_id, languages=languages)

        full_text = " ".join([
            entry.text if hasattr(entry, "text") else (entry.get("text", "") if isinstance(entry, dict) else str(entry))
            for entry in transcript_list
        ])
        if not full_text:
            return None

        cleaned = clean_transcript(full_text)
        if not cleaned or len(cleaned.strip()) < 15:
            logger.warning(f"[fetch_youtube_transcript] Conteúdo residual insuficiente após sanitização para {url}")
            return None

        return cleaned
    except Exception as e:
        logger.warning(f"[fetch_youtube_transcript] Falha ao extrair legendas para {url} (ID: {video_id}): {e}")
        return None


def generate_synthetic_lecture(tema: str) -> str:
    """Gera uma aula didática profunda e estruturada sobre o tema usando o LLM Tier Fast."""
    logger.info(f"[generate_synthetic_lecture] Gerando aula sintética com LLM para tema: '{tema}'")
    llm = get_llm(tier="fast", temperature=0.3, max_tokens=2500)
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "Você é um renomado professor especialista na preparação para concursos públicos e estudos de alto rendimento.\n"
            "Sua tarefa é ministrar uma aula completa, didática, aprofundada e fluida sobre o tema solicitado.\n"
            "A aula deve conter obrigatoriamente:\n"
            "1. Teoria Essencial: Conceito formal, fundamentação, divisões doutrinárias ou regras basilares.\n"
            "2. Macetes e Mnemônicos (Sacadas): Dicas práticas de memorização rápida e aplicação imediata.\n"
            "3. Pegadinhas de Prova: Armadilhas clássicas das principais bancas, detalhes sutis e exceções contra-intuitivas.\n"
            "4. Questões Comentadas de Fixação: Questões modelo com gabarito fundamentado.\n\n"
            "Mantenha o tom de uma aula falada de alta qualidade. Foque estritamente no tema solicitado."
        ),
        ("user", "Tema da aula: {tema}")
    ])
    try:
        chain = prompt | llm
        response = chain.invoke({"tema": tema})
        content = response.content if hasattr(response, "content") else str(response)
        return content.strip()
    except Exception as e:
        logger.error(f"[generate_synthetic_lecture] Erro ao gerar aula sintética via LLM: {e}. Usando texto básico.")
        return (
            f"Aula sobre {tema}.\n"
            f"1. Teoria: Aspectos fundamentais e princípios centrais de {tema}.\n"
            f"2. Sacada: Atenção aos conceitos-chave e definições formais de {tema}.\n"
            f"3. Pegadinha: Cuidado com a inversão de conceitos e generalizações indevidas em avaliações sobre {tema}.\n"
            f"4. Questão: Como o tema {tema} costuma ser cobrado em provas?"
        )


# =====================================================================
# NÓS DO GRAFO
# =====================================================================

def video_search_node(state: StudyState) -> Dict[str, Any]:
    """Busca ou recebe a aula a ser processada com fallback para extração de legendas ou geração sintética."""
    tema = state.get("tema_busca", "Conceito Geral")
    materia = state.get("materia")
    logger.info(f"[video_search] Buscando aula para o tema: '{tema}' (Matéria: {materia or 'a inferir'})")

    existing_video = state.get("video_encontrado") or {}
    link = existing_video.get("link")
    titulo = existing_video.get("titulo")
    transcricao = existing_video.get("transcricao")

    # 1. Se transcrição não foi fornecida
    if not transcricao:
        # Se há link do YouTube fornecido, tentar extrair legendas
        if link and ("youtube.com" in link or "youtu.be" in link):
            logger.info(f"[video_search] Tentando extrair legendas do YouTube: {link}")
            transcricao = fetch_youtube_transcript(link)
            if transcricao:
                logger.info(f"[video_search] Legenda extraída com sucesso ({len(transcricao)} caracteres).")

        # Se não obteve legenda ou não havia link real
        if not transcricao:
            logger.info(f"[video_search] Gerando aula sintética com LLM Tier Fast para '{tema}'...")
            transcricao = generate_synthetic_lecture(tema)

    if not link:
        slug = generate_video_slug(tema)
        link = f"https://youtube.com/watch?v=aula_{slug}"

    if not titulo:
        titulo = f"Aula Completa: {tema}"

    res = {
        "video_encontrado": {
            "link": link,
            "titulo": titulo,
            "transcricao": transcricao
        }
    }
    if materia:
        res["materia"] = materia
    return res


def validate_video_node(state: StudyState) -> Dict[str, Any]:
    """Verifica se a aula já foi processada no PostgreSQL."""
    video = state.get("video_encontrado", {})
    link = video.get("link")
    logger.info(f"[validate_video] Checando status do link: {link}")

    ja_processado = db.check_video_processed(link)
    if ja_processado:
        logger.warning(f"[validate_video] Vídeo {link} já processado anteriormente.")
        return {
            "video_valido": False,
            "db_status": f"Vídeo {link} já processado anteriormente. Encerrando fluxo sem duplicar."
        }

    raw_text = video.get("transcricao", "")
    logger.info("[validate_video] Aula inédita detectada. Prosseguindo para extração.")
    return {
        "video_valido": True,
        "raw_text": raw_text
    }


def route_after_validation(state: StudyState) -> str:
    """Roteia para extraction_agent se o vídeo for válido, ou encerra se já processado."""
    if state.get("video_valido", False):
        return "extraction_agent"
    return END


def extraction_agent_node(state: StudyState) -> Dict[str, Any]:
    """AGENTE EXTRAÇÃO (Tier Fast): Identifica conceitos, sacadas, pegadinhas e questões categorizando matéria e tópico."""
    raw_text = state.get("raw_text", "")
    logger.info("[extraction_agent] Iniciando extração de itens pedagógicos...")

    cleaned_text = clean_transcript(raw_text)
    if not cleaned_text or len(cleaned_text.strip()) < 15:
        logger.warning("[extraction_agent] Texto insuficiente ou vazio para extração.")
        return {"extracted_items": []}

    chunks = chunk_transcript(cleaned_text, chunk_size=10000, overlap=800)
    logger.info(f"[extraction_agent] Transcrição ({len(cleaned_text)} caracteres) dividida em {len(chunks)} bloco(s).")

    llm = get_llm(tier="fast", temperature=0.1, max_tokens=8192)
    structured_llm = llm.with_structured_output(ConjuntoItemsExtraidos)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "Você é um Especialista em Pedagogia e Análise Didática para Concursos e Estudos Avançados.\n"
            "Analise a transcrição da aula e extraia todos os itens pedagógicos relevantes.\n"
            "Para cada item, identifique com precisão:\n"
            "- 'materia': A disciplina ou matéria geral de concurso (ex: 'Língua Portuguesa', 'Direito Constitucional', 'Direito Administrativo', 'Informática').\n"
            "- 'topico': O assunto ou tópico específico abordado (ex: 'Crase', 'Controle de Constitucionalidade', 'Atos Administrativos').\n"
            "- 'categoria': Estritamente 'teoria', 'sacada', 'pegadinha' ou 'questao'.\n"
            "  * 'teoria': Conceito fundamental, regra geral ou doutrina.\n"
            "  * 'sacada': Mnemônico, atalho mental, macete ou dica de memorização.\n"
            "  * 'pegadinha': Armadilha de prova, exceção contra-intuitiva ou sutileza semântica.\n"
            "  * 'questao': Pergunta chave de fixação com sua resposta e explicação.\n\n"
            "Seja preciso e não resuma excessivamente o conhecimento essencial."
        ),
        ("user", "Matéria Informada: {materia_hint}\nTema da Aula: {tema}{bloco_info}\n\nTranscrição:\n{chunk_text}")
    ])

    all_extracted: List[Dict[str, Any]] = []
    materia_hint = state.get("materia") or "Inferir da transcrição e do tema"

    for idx, chunk in enumerate(chunks):
        bloco_info = f" (Bloco {idx + 1} de {len(chunks)})" if len(chunks) > 1 else ""
        try:
            logger.info(f"[extraction_agent] Processando bloco {idx + 1}/{len(chunks)} ({len(chunk)} caracteres)...")
            prompt_val = prompt.invoke({
                "materia_hint": materia_hint,
                "tema": state.get("tema_busca", "Geral"),
                "bloco_info": bloco_info,
                "chunk_text": chunk
            })
            result: ConjuntoItemsExtraidos = structured_llm.invoke(prompt_val)
            chunk_items = [item.model_dump() for item in result.items]
            logger.info(f"[extraction_agent] Bloco {idx + 1}/{len(chunks)}: {len(chunk_items)} itens extraídos.")
            all_extracted.extend(chunk_items)
        except Exception as e:
            logger.error(f"[extraction_agent] Erro ao extrair itens do bloco {idx + 1}/{len(chunks)}: {e}")

    # Normaliza a matéria de todos os itens extraídos para evitar variações
    for item in all_extracted:
        raw_mat = item.get("materia") or state.get("materia") or state.get("tema_busca")
        canonical_mat, _ = db.normalize_materia(raw_mat)
        item["materia"] = canonical_mat

    unique_items = deduplicate_extracted_items(all_extracted)
    logger.info(f"[extraction_agent] Total consolidado: {len(unique_items)} itens únicos extraídos.")

    # Identifica a matéria principal da aula
    detected_materia = state.get("materia")
    if not detected_materia and unique_items:
        detected_materia = unique_items[0].get("materia")
    if not detected_materia:
        detected_materia, _ = db.normalize_materia(state.get("tema_busca"))

    materia_id = db.get_or_create_materia(detected_materia)
    for item in unique_items:
        item["materia_id"] = materia_id
        if not item.get("materia"):
            item["materia"] = detected_materia

    return {
        "extracted_items": unique_items,
        "materia": detected_materia,
        "materia_id": materia_id
    }


def retrieve_similar_items_node(state: StudyState) -> Dict[str, Any]:
    """FACILITADOR RAG (pgvector): Localiza Tópico Mestre no PostgreSQL com particionamento por matéria."""
    tema = state.get("tema_busca", "Geral")
    materia = state.get("materia")
    materia_id = state.get("materia_id")
    extracted_items = state.get("extracted_items", [])

    if materia_id is None and materia:
        materia_id = db.get_or_create_materia(materia)

    logger.info(f"[retrieve_similar_items] Buscando Tópico Mestre e histórico para '{tema}' (Matéria ID: {materia_id})...")

    # 1. Busca Tópico Mestre (parent_id IS NULL) no espaço semântico particionado por matéria
    master_topic = None
    try:
        tema_vector = embedding_manager.embed_query(tema)
        master_topic = db.search_master_topic(
            embedding=tema_vector,
            min_similarity=0.70,
            materia_id=materia_id
        )
        if master_topic:
            logger.info(
                f"[retrieve_similar_items] Tópico Mestre identificado: ID {master_topic['id']} - "
                f"'{master_topic['topico']}' (Similaridade: {master_topic.get('similarity', 0):.2f}, Matéria: {master_topic.get('materia_nome')})"
            )
    except Exception as e:
        logger.error(f"[retrieve_similar_items] Erro ao buscar Tópico Mestre: {e}")

    relevant_items_map: Dict[int, Dict[str, Any]] = {}

    # Se encontramos um Tópico Mestre, ele é o ponto de ancoragem central
    if master_topic:
        relevant_items_map[master_topic["id"]] = master_topic

    # 2. Busca também para os itens candidatos individuais dentro da matéria para enriquecer o contexto
    for item in extracted_items:
        texto_busca = f"Tópico: {item.get('topico', '')} - {item.get('conteudo', '')}"
        try:
            vetor = embedding_manager.embed_query(texto_busca)
            similares = db.search_similar_items(
                embedding=vetor,
                limit=3,
                min_similarity=settings.MIN_SIMILARITY_THRESHOLD,
                materia_id=materia_id
            )
            for sim in similares:
                item_id = sim["id"]
                if item_id not in relevant_items_map:
                    relevant_items_map[item_id] = sim
        except Exception as e:
            logger.error(f"[retrieve_similar_items] Erro na busca vetorial para item '{item.get('topico')}': {e}")

    contexto = list(relevant_items_map.values())
    master_id = master_topic["id"] if master_topic else None
    logger.info(f"[retrieve_similar_items] {len(contexto)} itens históricos recuperados (Master ID: {master_id}).")
    return {
        "relevant_existing_items": contexto,
        "master_topic_id": master_id,
        "materia_id": materia_id,
        "materia": materia
    }


def reconciliation_agent_node(state: StudyState) -> Dict[str, Any]:
    """
    AGENTE RECONCILIADOR (Tier Mid): Curador Contínuo e Guardião de Não-Redundância.
    Regras de ouro:
      - Se o Tópico Mestre já existe no banco na mesma matéria:
        * Teoria repetida ou já explicada: DESCARTAR.
        * Pegadinha ou questão já existente no histórico: DESCARTAR.
        * Novas questões, pegadinhas e sacadas inéditas: ADICIONAR_FRAGMENTO referenciando o ID do Mestre.
      - Se o Tópico Mestre é inédito na matéria:
        * Cria o Tópico Mestre (CRIAR_NOVO).
        * Vincula as sacadas, pegadinhas e questões da aula a esse novo registro pai.
    """
    extracted = state.get("extracted_items", [])
    existing = state.get("relevant_existing_items", [])
    tema = state.get("tema_busca", "Geral")
    materia = state.get("materia", "Geral")
    master_id = state.get("master_topic_id")
    materia_id = state.get("materia_id")

    logger.info(f"[reconciliation_agent] Curando {len(extracted)} candidatos contra histórico (Matéria: {materia}, Master ID: {master_id})...")

    if not extracted:
        return {"reconciliation_decisions": []}

    try:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "Você é o Curador Chefe da Base de Conhecimento RAG de Estudos.\n"
                "Sua missão é RECONCILIAR novos itens candidatos com a base histórica do PostgreSQL, "
                "garantindo NÃO-REDUNDÂNCIA estrita e VÍNCULO HIERÁRQUICO a um Tópico Mestre da matéria indicada.\n\n"
                "DIRETRIZES DE RECONCILIAÇÃO:\n"
                "1. TÓPICO MESTRE EXISTENTE (quando informado no contexto):\n"
                "   - NUNCA crie um novo tópico mestre redundante.\n"
                "   - Teoria básica que apenas repete conceitos já arquivados no conteúdo base ou nos fragmentos DEVE ser 'DESCARTAR'.\n"
                "   - Pegadinhas, macetes ou questões que já estejam no histórico do tópico DEVEM ser 'DESCARTAR'.\n"
                "   - Questões de fixação, pegadinhas de prova e sacadas INÉDITAS que agreguem novidade pedagógica real DEVEM ser 'ADICIONAR_FRAGMENTO' com 'item_id_referencia' apontando para o Tópico Mestre.\n"
                "2. TÓPICO MESTRE INÉDITO (quando nenhum tópico existente corresponder ao tema):\n"
                "   - A teoria central deve ser 'CRIAR_NOVO' como Tópico Mestre da matéria.\n"
                "   - As sacadas, pegadinhas e questões da aula devem ser 'CRIAR_NOVO' vinculadas hierarquicamente ao Tópico Mestre.\n\n"
                "Forneça SEMPRE uma justificativa técnica e pedagógica precisa para cada decisão."
            ),
            (
                "user",
                "DISCIPLINA / MATÉRIA: {materia}\n"
                "TEMA DA AULA: {tema}\n"
                "ID DO TÓPICO MESTRE EXISTENTE: {master_id_str}\n\n"
                "ITENS HISTÓRICOS NO BANCO (com histórico de fragmentos e questões já gravadas):\n"
                "{existing_db}\n\n"
                "NOVOS ITENS CANDIDATOS:\n"
                "{extracted_candidates}\n\n"
                "Emita o relatório de reconciliação estruturado."
            )
        ])

        llm = get_llm(tier="mid", temperature=0.1, max_tokens=4096)
        structured_llm = llm.with_structured_output(RelatorioReconciliacao)
        prompt_val = prompt.invoke({
            "materia": materia,
            "tema": tema,
            "master_id_str": str(master_id) if master_id else "Nenhum (Matéria/Tópico Inédito)",
            "existing_db": json.dumps(existing, ensure_ascii=False, indent=2),
            "extracted_candidates": json.dumps(extracted, ensure_ascii=False, indent=2)
        })
        res: RelatorioReconciliacao = structured_llm.invoke(prompt_val)
        decisions = [d.model_dump() for d in res.decisoes]
    except Exception as e:
        logger.error(f"[reconciliation_agent] Falha no LLM de reconciliação: {e}. Aplicando contingência hierárquica.")
        decisions = []
        for item_dict in extracted:
            item_obj = ItemEstudo(**item_dict)
            if master_id is not None:
                # Tópico mestre já existe: teorias repetitivas são descartadas, sacadas/pegadinhas/questões agregadas
                if item_obj.categoria == "teoria":
                    decisions.append(DecisaoIntegracao(
                        item=item_obj,
                        acao="DESCARTAR",
                        justificativa=f"Teoria base já coberta no Tópico Mestre ID {master_id}."
                    ).model_dump())
                else:
                    decisions.append(DecisaoIntegracao(
                        item=item_obj,
                        acao="ADICIONAR_FRAGMENTO",
                        item_id_referencia=master_id,
                        conteudo_incremental=item_obj.conteudo,
                        justificativa=f"Novidade ({item_obj.categoria}) vinculada ao Tópico Mestre ID {master_id}."
                    ).model_dump())
            else:
                # Tópico inédito
                decisions.append(DecisaoIntegracao(
                    item=item_obj,
                    acao="CRIAR_NOVO",
                    justificativa="Matéria inédita na base de conhecimento."
                ).model_dump())

    logger.info(f"[reconciliation_agent] {len(decisions)} decisões de reconciliação processadas.")
    return {"reconciliation_decisions": decisions}


def db_writer_node(state: StudyState) -> Dict[str, Any]:
    """NÓ DE GRAVAÇÃO (UPSERT HIERÁRQUICO): Grava Tópicos Mestres e vincula questões, pegadinhas e sacadas com parent_id e materia_id."""
    decisoes = state.get("reconciliation_decisions", [])
    video = state.get("video_encontrado", {})
    link = video.get("link")
    titulo = video.get("titulo")
    transcricao = video.get("transcricao")
    tema = state.get("tema_busca", "Geral")
    materia = state.get("materia", "Geral")
    master_id = state.get("master_topic_id")
    materia_id = state.get("materia_id")

    # Garante materia_id
    if materia_id is None:
        materia_id = db.get_or_create_materia(materia)

    logger.info(f"[db_writer] Gravando dados para aula '{tema}' (Matéria ID: {materia_id}, Link: {link})...")

    # Registra vídeo processado com metadados e transcrição completa
    if link:
        try:
            db.save_processed_video(
                link=link,
                tema=tema,
                titulo=titulo,
                transcricao_completa=transcricao
            )
        except Exception as e:
            logger.error(f"[db_writer] Falha ao registrar vídeo processado: {e}")

    stats = {"criados": 0, "fragmentos": 0, "descartados": 0}
    current_master_id = master_id

    # 1. Se não há Tópico Mestre existente, elege/cria o Tópico Mestre primeiro
    if current_master_id is None:
        # Tenta localizar o primeiro item de teoria candidato para ser o Mestre
        master_dec = None
        for dec in decisoes:
            if dec.get("acao") == "CRIAR_NOVO" and dec.get("item", {}).get("categoria") == "teoria":
                master_dec = dec
                break

        if master_dec:
            item_data = master_dec.get("item", {})
            item_obj = ItemEstudo(**item_data)
            texto = f"Tópico: {item_obj.topico} - {item_obj.conteudo}"
            vetor = embedding_manager.embed_query(texto)
            current_master_id = db.insert_new_item(
                item=item_obj,
                embedding=vetor,
                link_do_video=link,
                parent_id=None,
                materia_id=materia_id
            )
            logger.info(f"[db_writer] Tópico Mestre Criado: ID {current_master_id} ('{item_obj.topico}', Matéria ID: {materia_id})")
            stats["criados"] += 1
            master_dec["_ja_processado_como_mestre"] = True
        elif decisoes:
            # Fallback: cria o mestre diretamente do tema_busca
            mestre_fallback = ItemEstudo(
                materia=materia,
                topico=tema,
                categoria="teoria",
                conteudo=f"Tópico de estudo sobre {tema}.",
                materia_id=materia_id
            )
            vetor = embedding_manager.embed_query(f"Tópico: {tema}")
            current_master_id = db.insert_new_item(
                item=mestre_fallback,
                embedding=vetor,
                link_do_video=link,
                parent_id=None,
                materia_id=materia_id
            )
            logger.info(f"[db_writer] Tópico Mestre Fallback Criado: ID {current_master_id} ('{tema}')")
            stats["criados"] += 1

    # 2. Processa todos os itens restantes vinculando ao current_master_id e materia_id
    for dec in decisoes:
        if dec.get("_ja_processado_como_mestre"):
            continue

        acao = dec.get("acao")
        item_data = dec.get("item", {})
        item_obj = ItemEstudo(**item_data)
        justificativa = dec.get("justificativa", "")
        conteudo_inc = dec.get("conteudo_incremental") or item_obj.conteudo
        ref_id = dec.get("item_id_referencia") or current_master_id

        if acao == "DESCARTAR":
            logger.info(f"[db_writer] DESCARTAR: Item '{item_obj.topico}' redundante.")
            stats["descartados"] += 1

        elif acao == "ADICIONAR_FRAGMENTO":
            target_id = ref_id or current_master_id
            if target_id is not None:
                texto = f"Tópico: {item_obj.topico} - {conteudo_inc}"
                vetor = embedding_manager.embed_query(texto)
                db.append_fragment_to_item(
                    item_id=target_id,
                    topico=item_obj.topico,
                    categoria=item_obj.categoria,
                    conteudo_incremental=conteudo_inc,
                    detalhes_resposta=item_obj.detalhes_resposta,
                    justificativa=justificativa,
                    link_do_video=link,
                    embedding=vetor,
                    materia_id=materia_id
                )
                logger.info(f"[db_writer] ADICIONAR_FRAGMENTO: Enriquecido ID {target_id} com '{item_obj.topico}'")
                stats["fragmentos"] += 1
            else:
                stats["descartados"] += 1

        elif acao == "CRIAR_NOVO":
            target_id = current_master_id
            texto = f"Tópico: {item_obj.topico} - {item_obj.conteudo}"
            vetor = embedding_manager.embed_query(texto)
            if target_id:
                db.append_fragment_to_item(
                    item_id=target_id,
                    topico=item_obj.topico,
                    categoria=item_obj.categoria,
                    conteudo_incremental=item_obj.conteudo,
                    detalhes_resposta=item_obj.detalhes_resposta,
                    justificativa=justificativa,
                    link_do_video=link,
                    embedding=vetor,
                    materia_id=materia_id
                )
                stats["fragmentos"] += 1
                logger.info(f"[db_writer] CRIAR_NOVO vinculado ao Mestre ID {target_id} (Matéria ID: {materia_id})")
            else:
                child_id = db.insert_new_item(
                    item=item_obj,
                    embedding=vetor,
                    link_do_video=link,
                    parent_id=None,
                    materia_id=materia_id
                )
                current_master_id = child_id
                stats["criados"] += 1
                logger.info(f"[db_writer] CRIAR_NOVO Novo Tópico Mestre ID {child_id}")

    status_msg = (
        f"Reconciliação concluída: {stats['criados']} tópicos mestres criados, "
        f"{stats['fragmentos']} itens/fragmentos vinculados, "
        f"{stats['descartados']} redundâncias descartadas."
    )
    logger.info(f"[db_writer] {status_msg}")

    return {
        "db_status": status_msg,
        "summary_stats": stats,
        "master_topic_id": current_master_id,
        "materia_id": materia_id,
        "materia": materia
    }


# =====================================================================
# MONTAGEM DO GRAFO LANGGRAPH
# =====================================================================

def build_study_graph():
    """Constrói e compila o StateGraph LangGraph do Study RAG Agent."""
    workflow = StateGraph(StudyState)

    # 1. Adiciona os nós
    workflow.add_node("video_search", video_search_node)
    workflow.add_node("validate_video", validate_video_node)
    workflow.add_node("extraction_agent", extraction_agent_node)
    workflow.add_node("retrieve_similar_items", retrieve_similar_items_node)
    workflow.add_node("reconciliation_agent", reconciliation_agent_node)
    workflow.add_node("db_writer", db_writer_node)

    # 2. Configura as arestas
    workflow.add_edge(START, "video_search")
    workflow.add_edge("video_search", "validate_video")

    # 3. Roteamento condicional pós-validação de vídeo
    workflow.add_conditional_edges(
        "validate_video",
        route_after_validation,
        {
            "extraction_agent": "extraction_agent",
            END: END
        }
    )

    # 4. Pipeline RAG & Reconciliação
    workflow.add_edge("extraction_agent", "retrieve_similar_items")
    workflow.add_edge("retrieve_similar_items", "reconciliation_agent")
    workflow.add_edge("reconciliation_agent", "db_writer")
    workflow.add_edge("db_writer", END)

    return workflow.compile()


study_graph = build_study_graph()
