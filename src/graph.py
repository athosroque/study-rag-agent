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
    DecisaoIntegracao,
    QuestaoCespe,
    ConjuntoQuestoesCespe
)
from src.llm import get_llm
from src.embeddings import embedding_manager
from src import db
from src import revisoes

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


def chunk_transcript(text: str, chunk_size: int = 5000, overlap: int = 800) -> List[str]:
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
    """Gera uma aula didática profunda e estruturada sobre o tema usando o LLM GLM 5.3 Flash."""
    logger.info(f"[generate_synthetic_lecture] Gerando aula sintética com LLM para tema: '{tema}'")
    llm = get_llm(model="glm-5.3-flash", temperature=0.3, max_tokens=2500)
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

    chunks = chunk_transcript(cleaned_text, chunk_size=5000, overlap=800)
    logger.info(f"[extraction_agent] Transcrição ({len(cleaned_text)} caracteres) dividida em {len(chunks)} bloco(s).")

    llm = get_llm(model="glm-5.3-flash", temperature=0.1, max_tokens=8192)
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
            "  * 'questao': Questão de fixação. ATENÇÃO ESTRITA:\n"
            "    - Coloque no campo 'conteudo' EXCLUSIVAMENTE o enunciado/pergunta da assertiva (NUNCA coloque a resposta dentro de 'conteudo'). O gabarito oficial ou resposta direta (ex: 'CERTO', 'ERRADO', 'Alternativa B', ou resposta objetiva concisa) DEVE ser colocado no campo 'gabarito'. Justificativas, resoluções comentadas e notas pedagógicas complementares DEVEM ir no campo 'detalhes_resposta'.\n"
            "    - REGRA CRÍTICA DE AUTOSSUFICIÊNCIA: O enunciado DEVE ser 100% autossuficiente e respondível de forma autônoma sem texto de apoio externo. NUNCA extraia perguntas que façam referências cegas a 'do texto', 'no texto', 'o autor', 'na linha X' a menos que a frase ou excerto completo analisado esteja integralmente transcrito dentro do próprio enunciado. Em matérias como Língua Portuguesa (colocação pronominal, crase, concordância, regência, pontuação), forneça OBRIGATORIAMENTE a oração ou frase completa na pergunta para que o estudante possa julgar a regra gramatical. Se o professor citou uma questão de slide cujo texto não foi lido ou não está presente, NÃO extraia como questão truncada: reformule a pergunta para que fique conceitualmente autônoma (ex: 'Considerando uma oração em que haja termo invariável atrativo antes do verbo...'), ou extraia o conhecimento como 'teoria' ou 'pegadinha'.\n\n"
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
            try:
                result: ConjuntoItemsExtraidos = structured_llm.invoke(prompt_val)
                chunk_items = [item.model_dump() for item in result.items]
            except Exception as e:
                logger.warning(f"[extraction_agent] Pydantic parser falhou, tentando fallback manual. Erro: {e}")
                # Fazendo request manual e limpando markdown
                raw_llm = get_llm(model="glm-5.3-flash", temperature=0.1, max_tokens=8192)
                raw_response = raw_llm.invoke(prompt_val)
                text = raw_response.content
                import re
                match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
                if match:
                    text = match.group(1)
                try:
                    data = json.loads(text)
                    # Verifica se data é uma lista (formato que alguns modelos devolvem)
                    if isinstance(data, list):
                        result = ConjuntoItemsExtraidos(items=data)
                    else:
                        result = ConjuntoItemsExtraidos.model_validate(data)
                    chunk_items = [item.model_dump() for item in result.items]
                except Exception as parse_err:
                    logger.error(f"[extraction_agent] Fallback falhou também: {parse_err}")
                    chunk_items = []
            
            logger.info(f"[extraction_agent] Bloco {idx + 1}/{len(chunks)}: {len(chunk_items)} itens extraídos.")
            all_extracted.extend(chunk_items)
        except Exception as e:
            logger.error(f"[extraction_agent] Erro fatal no processamento do bloco {idx + 1}/{len(chunks)}: {e}")

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
                "   - As sacadas, pegadinhas e questões da aula devem ser 'CRIAR_NOVO' vinculadas hierarquicamente ao Tópico Mestre.\n"
                "3. CURADORIA DE AUTOSSUFICIÊNCIA EM QUESTÕES:\n"
                "   - Toda questão mantida ('ADICIONAR_FRAGMENTO' ou 'CRIAR_NOVO') deve ter enunciado autossuficiente e respondível sem texto externo de apoio. Se a questão contiver referências órfãs a 'do texto', 'no texto', 'na linha X' sem a oração/frase suporte transcrita no corpo da pergunta, REFORMULE em 'conteudo_incremental' fornecendo a contextualização autônoma necessária, ou tome a ação 'DESCARTAR' se for impossível responder sem o slide do professor.\n\n"
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
                "Emita o relatório de reconciliação estruturado informando o 'indice_candidato' (posição numérica na lista de 0 a N) para identificar a qual candidato a decisão se refere."
            )
        ])

        decisions = []
        import concurrent.futures

        def process_batch(batch):
            indexed_batch = [{"indice_candidato": i, **item} for i, item in enumerate(batch)]
            prompt_val = prompt.invoke({
                "materia": materia,
                "tema": tema,
                "master_id_str": str(master_id) if master_id else "Nenhum (Matéria/Tópico Inédito)",
                "existing_db": json.dumps(existing, ensure_ascii=False, indent=2),
                "extracted_candidates": json.dumps(indexed_batch, ensure_ascii=False, indent=2)
            })
            llm = get_llm(model="glm-5.3-flash", temperature=0.1, max_tokens=4096)
            structured_llm = llm.with_structured_output(RelatorioReconciliacao)
            try:
                res: RelatorioReconciliacao = structured_llm.invoke(prompt_val)
                final_decisions = []
                for d in res.decisoes:
                    d_dict = d.model_dump()
                    idx = d_dict.pop("indice_candidato", -1)
                    if 0 <= idx < len(batch):
                        d_dict["item"] = batch[idx]
                        final_decisions.append(d_dict)
                return final_decisions
            except Exception as e:
                logger.warning(f"[reconciliation_agent] Pydantic parser falhou no batch, tentando fallback manual. Erro: {e}")
                raw_llm = get_llm(model="glm-5.3-flash", temperature=0.1, max_tokens=4096)
                raw_response = raw_llm.invoke(prompt_val)
                text = raw_response.content
                import re
                match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
                if match:
                    text = match.group(1)
                try:
                    data = json.loads(text)
                    if isinstance(data, list):
                        res = RelatorioReconciliacao(decisoes=data)
                    elif "decisoes" in data:
                        res = RelatorioReconciliacao.model_validate(data)
                    else:
                        res = RelatorioReconciliacao(decisoes=[])
                    
                    final_decisions = []
                    for d in res.decisoes:
                        d_dict = d.model_dump()
                        idx = d_dict.pop("indice_candidato", -1)
                        if 0 <= idx < len(batch):
                            d_dict["item"] = batch[idx]
                            final_decisions.append(d_dict)
                    return final_decisions
                except Exception as parse_err:
                    logger.error(f"[reconciliation_agent] Falha no fallback do batch: {parse_err}")
                    raise

        batch_size = 5
        batches = [extracted[i:i + batch_size] for i in range(0, len(extracted), batch_size)]
        logger.info(f"[reconciliation_agent] Processando {len(batches)} lote(s) em paralelo (tamanho máx: {batch_size}).")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_batch = {executor.submit(process_batch, b): i for i, b in enumerate(batches)}
            for future in concurrent.futures.as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                try:
                    batch_decisions = future.result()
                    decisions.extend(batch_decisions)
                    logger.info(f"[reconciliation_agent] Lote {batch_idx+1}/{len(batches)} concluído com {len(batch_decisions)} decisões.")
                except Exception as exc:
                    logger.error(f"[reconciliation_agent] Lote {batch_idx+1}/{len(batches)} gerou exceção: {exc}")
                    raise exc
    except Exception as e:
        logger.error(f"[reconciliation_agent] Falha no LLM de reconciliação: {e}. Aplicando contingência hierárquica.")
        decisions = []
        for item_dict in extracted:
            item_obj = ItemEstudo(**item_dict)
            if master_id is not None:
                # Tópico mestre já existe: teorias repetitivas são descartadas, sacadas/pegadinhas/questões agregadas
                if item_obj.categoria == "teoria":
                    decisions.append({
                        "item": item_dict,
                        "acao": "DESCARTAR",
                        "justificativa": f"Teoria base já coberta no Tópico Mestre ID {master_id}."
                    })
                else:
                    decisions.append({
                        "item": item_dict,
                        "acao": "ADICIONAR_FRAGMENTO",
                        "item_id_referencia": master_id,
                        "conteudo_incremental": item_obj.conteudo,
                        "justificativa": f"Novidade ({item_obj.categoria}) vinculada ao Tópico Mestre ID {master_id}."
                    })
            else:
                # Tópico inédito
                decisions.append({
                    "item": item_dict,
                    "acao": "CRIAR_NOVO",
                    "justificativa": "Matéria inédita na base de conhecimento."
                })

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
    masters_by_topic: Dict[str, int] = {}
    active_master_ids = set()

    if current_master_id is not None:
        active_master_ids.add(current_master_id)
        try:
            m_item = db.get_item_by_id(current_master_id)
            if m_item and m_item.get("topico"):
                masters_by_topic[m_item["topico"].strip().lower()] = current_master_id
        except Exception:
            pass

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
            active_master_ids.add(current_master_id)
            masters_by_topic[item_obj.topico.strip().lower()] = current_master_id
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
            active_master_ids.add(current_master_id)
            masters_by_topic[tema.strip().lower()] = current_master_id
            logger.info(f"[db_writer] Tópico Mestre Fallback Criado: ID {current_master_id} ('{tema}')")
            stats["criados"] += 1

    # 2. Processa todos os itens restantes vinculando ao mestre correspondente do seu tópico
    for dec in decisoes:
        if dec.get("_ja_processado_como_mestre"):
            continue

        acao = dec.get("acao")
        item_data = dec.get("item", {})
        item_obj = ItemEstudo(**item_data)
        justificativa = dec.get("justificativa", "")
        conteudo_inc = dec.get("conteudo_incremental") or item_obj.conteudo
        ref_id = dec.get("item_id_referencia")

        if acao == "DESCARTAR":
            logger.info(f"[db_writer] DESCARTAR: Item '{item_obj.topico}' redundante.")
            stats["descartados"] += 1

        elif acao == "ADICIONAR_FRAGMENTO":
            # Tenta resolver o target: ref_id explícito ou correspondência no mapa de tópicos mestres
            target_id = ref_id
            if target_id is None:
                item_topic_clean = (item_obj.topico or "").strip().lower()
                target_id = masters_by_topic.get(item_topic_clean)
                if target_id is None:
                    for t_name, m_id in masters_by_topic.items():
                        if t_name in item_topic_clean or item_topic_clean in t_name:
                            target_id = m_id
                            break
            if target_id is None:
                target_id = current_master_id

            if target_id is not None:
                texto = f"Tópico: {item_obj.topico} - {conteudo_inc}"
                vetor = embedding_manager.embed_query(texto)
                db.append_fragment_to_item(
                    item_id=target_id,
                    topico=item_obj.topico,
                    categoria=item_obj.categoria,
                    conteudo_incremental=conteudo_inc,
                    gabarito=item_obj.gabarito,
                    detalhes_resposta=item_obj.detalhes_resposta,
                    justificativa=justificativa,
                    link_do_video=link,
                    embedding=vetor,
                    materia_id=materia_id
                )
                active_master_ids.add(target_id)
                logger.info(f"[db_writer] ADICIONAR_FRAGMENTO: Enriquecido ID {target_id} com '{item_obj.topico}'")
                stats["fragmentos"] += 1
            else:
                stats["descartados"] += 1

        elif acao == "CRIAR_NOVO":
            texto = f"Tópico: {item_obj.topico} - {item_obj.conteudo}"
            vetor = embedding_manager.embed_query(texto)
            item_topic_clean = (item_obj.topico or "").strip().lower()

            # Se for teoria de um tópico novo e independente, cria como Tópico Mestre
            eh_novo_mestre = (
                item_obj.categoria == "teoria" and
                item_topic_clean not in masters_by_topic and
                not any(t in item_topic_clean or item_topic_clean in t for t in masters_by_topic)
            )

            if eh_novo_mestre:
                new_m_id = db.insert_new_item(
                    item=item_obj,
                    embedding=vetor,
                    link_do_video=link,
                    parent_id=None,
                    materia_id=materia_id
                )
                active_master_ids.add(new_m_id)
                masters_by_topic[item_topic_clean] = new_m_id
                stats["criados"] += 1
                logger.info(f"[db_writer] CRIAR_NOVO Novo Tópico Mestre Independente ID {new_m_id} ('{item_obj.topico}')")
            else:
                # Procura o mestre temático mais apropriado
                target_id = ref_id
                if target_id is None:
                    target_id = masters_by_topic.get(item_topic_clean)
                    if target_id is None:
                        for t_name, m_id in masters_by_topic.items():
                            if t_name in item_topic_clean or item_topic_clean in t_name:
                                target_id = m_id
                                break
                if target_id is None:
                    target_id = current_master_id

                if target_id:
                    db.append_fragment_to_item(
                        item_id=target_id,
                        topico=item_obj.topico,
                        categoria=item_obj.categoria,
                        conteudo_incremental=item_obj.conteudo,
                        gabarito=item_obj.gabarito,
                        detalhes_resposta=item_obj.detalhes_resposta,
                        justificativa=justificativa,
                        link_do_video=link,
                        embedding=vetor,
                        materia_id=materia_id
                    )
                    active_master_ids.add(target_id)
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
                    active_master_ids.add(child_id)
                    masters_by_topic[item_topic_clean] = child_id
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
        "active_master_ids": list(active_master_ids),
        "materia_id": materia_id,
        "materia": materia
    }


# =====================================================================
# NOVO AGENTE: GERADOR DE QUESTÕES CESPE/CEBRASPE (GLM 5.3 Flash)
# =====================================================================

def cespe_agent_node(state: StudyState) -> Dict[str, Any]:
    """
    AGENTE CESPE/CEBRASPE (GLM 5.3 Flash):
    Itera sobre todos os Tópicos Mestres ativados/criados na aula,
    identifica lacunas pedagógicas e formula questões inéditas de nível médio/alto.
    """
    master_id_default = state.get("master_topic_id")
    active_master_ids = state.get("active_master_ids", [])
    
    if not active_master_ids and master_id_default is not None:
        active_master_ids = [master_id_default]

    tema_geral = state.get("tema_busca", "Geral")
    materia_geral = state.get("materia", "Geral")
    materia_id_geral = state.get("materia_id")
    video = state.get("video_encontrado") or {}
    link = video.get("link")

    logger.info(f"[cespe_agent] Iniciando formulação de questões CESPE para {len(active_master_ids)} tópicos ativos...")

    llm = get_llm(model="glm-5.3-flash", temperature=0.2, max_tokens=4096)
    structured_llm = llm.with_structured_output(ConjuntoQuestoesCespe)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "Você é um Examinador Sênior e Especialista de Bancas de Concurso Público, com foco estrito no padrão CESPE / CEBRASPE.\n\n"
            "Sua missão é analisar o acervo pedagógico acumulado no banco de dados para um tópico específico e gerar NOVAS questões inéditas no formato CERTO ou ERRADO.\n\n"
            "DIRETRIZES FUNDAMENTAIS DA BANCA CESPE/CEBRASPE:\n"
            "1. INEDITISMO E NÃO-DUPLICAÇÃO: Verifique atentamente a lista de 'Questões Já Existentes'. É expressamente proibido formular questões redundantes ou repetições literais das já cadastradas.\n"
            "2. NÍVEL DE DIFICULDADE (MÉDIO/ALTO): Gere APENAS questões que exijam profundidade, conhecimento de jurisprudência ou entendimento de exceções e pegadinhas. Ignore completamente conceitos básicos, literais ou introdutórios (eles não precisam virar questão).\n"
            "3. QUANTIDADE DE QUESTÕES: Gere de 1 a 5 questões por tópico. Deve cobrir a teoria, as sacadas e as pegadinhas relevantes. Jamais exceda o limite rígido de 5 questões por tópico.\n"
            "4. PADRÃO CESPE/CEBRASPE (Assertiva Certo/Errado):\n"
            "   - O enunciado deve ser uma declaração/assertiva categórica para o candidato julgar como CERTO ou ERRADO.\n"
            "   - Explore vocabulário clássico de prova, termos restritivos/ampliativos ('sempre', 'nunca', 'exclusivamente', 'independente de', 'salvo se'), inversões conceituais sutis e exceções.\n"
            "5. GABARITO E FUNDAMENTAÇÃO PEDAGÓGICA:\n"
            "   - 'gabarito': Estritamente 'CERTO' ou 'ERRADO'.\n"
            "   - 'justificativa': Explicação técnica aprofundada demonstrando o porquê do gabarito (base legal, doutrinária ou jurisprudencial).\n"
            "   - 'pegadinha_explicada': Análise minuciosa de onde reside a armadilha na assertiva e como a banca induz o candidato ao erro.\n"
            "6. AUTOSSUFICIÊNCIA DO ENUNCIADO:\n"
            "   - O candidato julga a questão isoladamente, sem acesso a textos externos.\n"
            "   - É EXPRESSAMENTE PROIBIDO criar enunciados que digam 'no texto', 'segundo o autor', 'na linha X', a menos que um trecho de apoio esteja integralmente transcrito no próprio corpo do enunciado."
        ),
        (
            "user",
            "Disciplina/Matéria: {materia}\n"
            "Tema do Tópico: {tema_topico}\n\n"
            "--- CONHECIMENTO TEÓRICO ACUMULADO NO BANCO ---\n"
            "{teoria_texto}\n\n"
            "--- SACADAS E MNEMÔNICOS NO BANCO ---\n"
            "{sacadas_texto}\n\n"
            "--- PEGADINHAS JÁ MAPEADAS NO BANCO ---\n"
            "{pegadinhas_texto}\n\n"
            "--- QUESTÕES JÁ EXISTENTES (NÃO REPETIR) ---\n"
            "{questoes_existentes_texto}\n\n"
            "Gere agora questões inéditas de nível médio/alto cobrindo os pontos essenciais e pegadinhas deste tópico."
        )
    ])

    questoes_salvas: List[Dict[str, Any]] = []

    for master_id in active_master_ids:
        master_item = None
        if master_id:
            try:
                master_item = db.get_item_by_id(master_id)
            except Exception as e:
                logger.warning(f"[cespe_agent] Não foi possível carregar item mestre {master_id}: {e}")
                continue
        
        if not master_item:
            continue

        tema_topico = master_item.get('topico', tema_geral)
        materia_topico = master_item.get("materia_nome") or materia_geral
        materia_id_topico = master_item.get("materia_id") or materia_id_geral

        logger.info(f"[cespe_agent] Processando Tópico ID {master_id}: '{tema_topico}'")

        contexto_teorico: List[str] = [f"Tópico Central: {tema_topico} - {master_item.get('conteudo')}"]
        questoes_existentes: List[str] = []
        pegadinhas_existentes: List[str] = []
        sacadas_existentes: List[str] = []

        # Fragmentos no JSONB
        for frag in master_item.get("fragmentos", []):
            cat = frag.get("categoria", "")
            texto = frag.get("conteudo_incremental", "")
            if cat == "questao":
                questoes_existentes.append(f"- Assertiva: {texto} | Resposta: {frag.get('detalhes_resposta', '')}")
            elif cat == "pegadinha":
                pegadinhas_existentes.append(f"- Armadilha: {texto}")
            elif cat == "sacada":
                sacadas_existentes.append(f"- Mnemônico/Sacada: {texto}")
            else:
                contexto_teorico.append(f"- Teoria Adicional: {texto}")

        # Filhos relacionais
        for filho in master_item.get("filhos", []):
            cat = filho.get("categoria", "")
            conteudo = filho.get("conteudo", "")
            if cat == "questao":
                questoes_existentes.append(f"- Assertiva: {conteudo} | Detalhes: {filho.get('detalhes_resposta', '')}")
            elif cat == "pegadinha":
                pegadinhas_existentes.append(f"- Armadilha: {conteudo}")
            elif cat == "sacada":
                sacadas_existentes.append(f"- Mnemônico/Sacada: {conteudo}")
            else:
                contexto_teorico.append(f"- Teoria: {filho.get('topico')} — {conteudo}")

        teoria_str = "\n".join(contexto_teorico) if contexto_teorico else f"Tópico sobre {tema_topico}."
        sacadas_str = "\n".join(sacadas_existentes) if sacadas_existentes else "Nenhuma sacada registrada."
        pegadinhas_str = "\n".join(pegadinhas_existentes) if pegadinhas_existentes else "Nenhuma pegadinha registrada."
        questoes_str = "\n".join(questoes_existentes) if questoes_existentes else "Nenhuma questão cadastrada previamente."

        try:
            prompt_val = prompt.invoke({
                "materia": materia_topico,
                "tema_topico": tema_topico,
                "teoria_texto": teoria_str,
                "sacadas_texto": sacadas_str,
                "pegadinhas_texto": pegadinhas_str,
                "questoes_existentes_texto": questoes_str
            })
            resultado: ConjuntoQuestoesCespe = structured_llm.invoke(prompt_val)
            logger.info(f"[cespe_agent] {len(resultado.questoes)} questões formuladas para o Tópico ID {master_id}.")

            # Persiste cada questão no PostgreSQL
            for q in resultado.questoes:
                detalhes = (
                    f"**Gabarito:** [{q.gabarito}]\n\n"
                    f"**Justificativa:**\n{q.justificativa}\n\n"
                    f"**Pegadinha da Banca:**\n{q.pegadinha_explicada}"
                )
                item_questao = ItemEstudo(
                    materia=materia_topico,
                    topico=q.topico or tema_topico,
                    categoria="questao",
                    conteudo=q.enunciado,
                    gabarito=q.gabarito,
                    detalhes_resposta=detalhes,
                    parent_id=master_id,
                    materia_id=materia_id_topico
                )
                texto_vetor = f"Tópico: {item_questao.topico} - {item_questao.conteudo}"
                vetor = embedding_manager.embed_query(texto_vetor)

                db.append_fragment_to_item(
                    item_id=master_id,
                    topico=item_questao.topico,
                    categoria="questao",
                    conteudo_incremental=item_questao.conteudo,
                    gabarito=item_questao.gabarito,
                    detalhes_resposta=detalhes,
                    justificativa=f"Questão CESPE: {q.pegadinha_explicada}",
                    link_do_video=link,
                    embedding=vetor,
                    materia_id=materia_id_topico
                )

                questoes_salvas.append({
                    "topico": q.topico,
                    "enunciado": q.enunciado,
                    "gabarito": q.gabarito,
                    "justificativa": q.justificativa,
                    "pegadinha_explicada": q.pegadinha_explicada
                })

        except Exception as e:
            logger.error(f"[cespe_agent] Erro na geração/persistência de questões para o Tópico ID {master_id}: {e}")

    logger.info(f"[cespe_agent] Total de {len(questoes_salvas)} questões geradas e salvas em {len(active_master_ids)} tópicos.")
    return {
        "cespe_questions": questoes_salvas
    }


def revisao_scheduler_node(state: StudyState) -> Dict[str, Any]:
    """
    NÓ AGENDADOR DE REVISÕES ESPAÇADAS (Ebbinghaus):
    Executa o agendamento da primeira revisão (Etapa 0) exclusivamente para questões (categoria='questao'),
    garantindo que teorias fiquem disponíveis apenas sob demanda.
    """
    logger.info("[revisao_scheduler] Agendando primeiras revisões exclusivamente para questões cadastradas...")
    try:
        res = revisoes.backfill_revisoes(categorias=["questao"], base_date=revisoes.agora_local())
        logger.info(f"[revisao_scheduler] Agendamento concluído: {res.get('revisoes_criadas', 0)} novas revisões de questões criadas.")
        return {"revisoes_agendadas": res}
    except Exception as e:
        logger.error(f"[revisao_scheduler] Falha ao agendar revisões: {e}")
        return {"revisoes_agendadas": {"erro": str(e), "revisoes_criadas": 0}}


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
    workflow.add_node("cespe_agent", cespe_agent_node)
    workflow.add_node("revisao_scheduler", revisao_scheduler_node)

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

    # 4. Pipeline RAG, Reconciliação, Questões CESPE & Agendamento de Revisões
    workflow.add_edge("extraction_agent", "retrieve_similar_items")
    workflow.add_edge("retrieve_similar_items", "reconciliation_agent")
    workflow.add_edge("reconciliation_agent", "db_writer")
    workflow.add_edge("db_writer", "cespe_agent")
    workflow.add_edge("cespe_agent", "revisao_scheduler")
    workflow.add_edge("revisao_scheduler", END)

    return workflow.compile()


study_graph = build_study_graph()
