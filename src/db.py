import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor
from src.config import settings
from src.models import ItemEstudo

logger = logging.getLogger(__name__)


# Dicionário canônico de matérias frequentes em concursos para evitar sinônimos redundantes
CANONICAL_MATERIAS_MAP: Dict[str, Tuple[str, str]] = {
    # Português / Gramática
    "portugues": ("Língua Portuguesa", "lingua_portuguesa"),
    "lingua portuguesa": ("Língua Portuguesa", "lingua_portuguesa"),
    "gramatica": ("Língua Portuguesa", "lingua_portuguesa"),
    "redacao": ("Língua Portuguesa", "lingua_portuguesa"),
    "linguagens": ("Língua Portuguesa", "lingua_portuguesa"),
    "crase": ("Língua Portuguesa", "lingua_portuguesa"),
    "regencia": ("Língua Portuguesa", "lingua_portuguesa"),
    "concordancia": ("Língua Portuguesa", "lingua_portuguesa"),
    "sintaxe": ("Língua Portuguesa", "lingua_portuguesa"),
    "morfologia": ("Língua Portuguesa", "lingua_portuguesa"),
    "ortografia": ("Língua Portuguesa", "lingua_portuguesa"),
    
    # Direito Constitucional
    "direito constitucional": ("Direito Constitucional", "direito_constitucional"),
    "dir constitucional": ("Direito Constitucional", "direito_constitucional"),
    "constituicao": ("Direito Constitucional", "direito_constitucional"),
    "constitucional": ("Direito Constitucional", "direito_constitucional"),

    # Direito Administrativo
    "direito administrativo": ("Direito Administrativo", "direito_administrativo"),
    "dir administrativo": ("Direito Administrativo", "direito_administrativo"),
    "administrativo": ("Direito Administrativo", "direito_administrativo"),

    # RLM / Matemática
    "raciocinio logico": ("Raciocínio Lógico e Matemática", "raciocinio_logico_matematico"),
    "raciocinio logico matematico": ("Raciocínio Lógico e Matemática", "raciocinio_logico_matematico"),
    "rlm": ("Raciocínio Lógico e Matemática", "raciocinio_logico_matematico"),
    "matematica": ("Raciocínio Lógico e Matemática", "raciocinio_logico_matematico"),
    "logica": ("Raciocínio Lógico e Matemática", "raciocinio_logico_matematico"),

    # Informática / TI
    "informatica": ("Informática", "informatica"),
    "tecnologia da informacao": ("Informática", "informatica"),
    "ti": ("Informática", "informatica"),

    # Direito Penal
    "direito penal": ("Direito Penal", "direito_penal"),
    "dir penal": ("Direito Penal", "direito_penal"),
    "penal": ("Direito Penal", "direito_penal"),

    # Processual Penal
    "direito processual penal": ("Direito Processual Penal", "direito_processual_penal"),
    "processo penal": ("Direito Processual Penal", "direito_processual_penal"),
    "dpp": ("Direito Processual Penal", "direito_processual_penal"),

    # Direito Civil
    "direito civil": ("Direito Civil", "direito_civil"),
    "dir civil": ("Direito Civil", "direito_civil"),
    "civil": ("Direito Civil", "direito_civil"),

    # Processual Civil
    "direito processual civil": ("Direito Processual Civil", "direito_processual_civil"),
    "processo civil": ("Direito Processual Civil", "direito_processual_civil"),
    "dpc": ("Direito Processual Civil", "direito_processual_civil"),

    # Administração
    "administracao publica": ("Administração Pública", "administracao_publica"),
    "administracao geral": ("Administração Pública", "administracao_publica"),
    "conhecimentos bancarios": ("Conhecimentos Bancários", "conhecimentos_bancarios"),
}


def clean_slug_text(text: str) -> str:
    """Gera um slug normalizado sem acentos, pontuação e com underscores."""
    if not text:
        return "geral"
    normalized = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', normalized.strip().lower()).strip('_')
    return slug or "geral"


def normalize_materia(nome: Optional[str]) -> Tuple[str, str]:
    """
    Normaliza o nome da matéria para evitar duplicidades e variações léxicas/ortográficas.
    Retorna uma tupla (nome_canonico, slug).
    Exemplos:
      - 'portugues' -> ('Língua Portuguesa', 'lingua_portuguesa')
      - 'Português' -> ('Língua Portuguesa', 'lingua_portuguesa')
      - '  LÍNGUA PORTUGUESA  ' -> ('Língua Portuguesa', 'lingua_portuguesa')
      - 'Direito Tributário' -> ('Direito Tributário', 'direito_tributario')
    """
    if not nome or not nome.strip():
        return ("Geral", "geral")

    raw_clean = nome.strip()
    key = unicodedata.normalize('NFKD', raw_clean).encode('ASCII', 'ignore').decode('utf-8').lower()
    key = re.sub(r'[^a-z0-9 ]+', ' ', key).strip()
    key = re.sub(r'\s+', ' ', key)

    if key in CANONICAL_MATERIAS_MAP:
        return CANONICAL_MATERIAS_MAP[key]

    # Normalização genérica para matérias não mapeadas
    slug = clean_slug_text(raw_clean)
    words = raw_clean.split()
    lowers = {"de", "da", "do", "das", "dos", "e", "em", "para", "com"}
    title_words = [
        w.lower() if w.lower() in lowers and i > 0 else w.capitalize()
        for i, w in enumerate(words)
    ]
    formatted_name = " ".join(title_words)
    return (formatted_name, slug)


def get_db_connection():
    """Retorna uma conexão nova com o PostgreSQL."""
    return psycopg2.connect(**settings.db_params)


def init_db():
    """Inicializa as tabelas, relacionamentos e extensões no PostgreSQL."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Habilita pgvector
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            # 2. Cria tabela normalizada de matérias
            cur.execute("""
                CREATE TABLE IF NOT EXISTS materias (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    criado_em TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)

            # 3. Cria tabela itens_estudo com coluna vetorial e JSONB para fragmentos
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS itens_estudo (
                    id SERIAL PRIMARY KEY,
                    topico TEXT NOT NULL,
                    categoria TEXT NOT NULL,
                    conteudo TEXT NOT NULL,
                    detalhes_resposta TEXT,
                    fragmentos JSONB DEFAULT '[]'::jsonb,
                    criado_em TIMESTAMP NOT NULL,
                    data_ultima_revisao TIMESTAMP NOT NULL,
                    qtd_revisoes INTEGER DEFAULT 0,
                    link_do_video TEXT,
                    embedding vector({settings.EMBEDDING_DIM}),
                    parent_id INTEGER REFERENCES itens_estudo(id) ON DELETE CASCADE,
                    materia_id INTEGER REFERENCES materias(id) ON DELETE SET NULL
                );
            """)

            # 4. Tabela de controle de fluxo de vídeos
            cur.execute("""
                CREATE TABLE IF NOT EXISTS videos_processados (
                    link_do_video TEXT PRIMARY KEY,
                    tema_busca TEXT,
                    titulo TEXT,
                    transcricao_completa TEXT,
                    data_processamento TIMESTAMP NOT NULL
                );
            """)

            # Migrações idempotentes para bases existentes
            cur.execute("ALTER TABLE videos_processados ADD COLUMN IF NOT EXISTS titulo TEXT;")
            cur.execute("ALTER TABLE videos_processados ADD COLUMN IF NOT EXISTS transcricao_completa TEXT;")
            cur.execute("ALTER TABLE itens_estudo ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES itens_estudo(id) ON DELETE CASCADE;")
            cur.execute("ALTER TABLE itens_estudo ADD COLUMN IF NOT EXISTS materia_id INTEGER REFERENCES materias(id) ON DELETE SET NULL;")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_itens_estudo_parent_id ON itens_estudo(parent_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_itens_estudo_materia_id ON itens_estudo(materia_id);")

            # 5. Índice vetorial HNSW para consultas de alta performance por similaridade de cosseno
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_itens_estudo_embedding 
                ON itens_estudo USING hnsw (embedding vector_cosine_ops);
            """)

            # 6. Migração de saneamento para dados legados sem materia_id
            # Garante que a matéria padrão 'Língua Portuguesa' exista
            cur.execute("""
                INSERT INTO materias (nome, slug)
                VALUES ('Língua Portuguesa', 'lingua_portuguesa')
                ON CONFLICT (slug) DO NOTHING;
            """)
            cur.execute("""
                UPDATE itens_estudo 
                SET materia_id = (SELECT id FROM materias WHERE slug = 'lingua_portuguesa')
                WHERE materia_id IS NULL;
            """)

            # 7. Tabela de Revisão Espaçada (Curva do Esquecimento - Ebbinghaus)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS revisoes (
                    id SERIAL PRIMARY KEY,
                    item_id INTEGER NOT NULL REFERENCES itens_estudo(id) ON DELETE CASCADE,
                    etapa INTEGER NOT NULL DEFAULT 0,
                    intervalo_dias INTEGER NOT NULL,
                    data_agendada TIMESTAMP NOT NULL,
                    data_realizada TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'pendente',
                    desempenho INTEGER,
                    observacao TEXT,
                    criado_em TIMESTAMP NOT NULL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_revisoes_item_id ON revisoes(item_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_revisoes_pendentes ON revisoes(status, data_agendada);")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_unica_revisao_aberta ON revisoes (item_id) WHERE status IN ('pendente', 'adiada');")
            cur.execute("ALTER TABLE revisoes ADD COLUMN IF NOT EXISTS observacao TEXT;")

            conn.commit()
            logger.info("Banco de dados PostgreSQL + pgvector inicializado com sucesso (com suporte a matérias e revisões espaçadas).")
    finally:
        conn.close()


def check_video_processed(link: str) -> bool:
    """Verifica se o link do vídeo já foi registrado na base."""
    if not link:
        return False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM videos_processados WHERE link_do_video = %s;", (link,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def save_processed_video(
    link: str,
    tema: str,
    titulo: Optional[str] = None,
    transcricao_completa: Optional[str] = None
):
    """Registra o vídeo processado na tabela de controle com título e transcrição completa."""
    if not link:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO videos_processados (link_do_video, tema_busca, titulo, transcricao_completa, data_processamento)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (link_do_video) DO UPDATE SET
                    tema_busca = EXCLUDED.tema_busca,
                    titulo = COALESCE(EXCLUDED.titulo, videos_processados.titulo),
                    transcricao_completa = COALESCE(EXCLUDED.transcricao_completa, videos_processados.transcricao_completa),
                    data_processamento = EXCLUDED.data_processamento;
            """, (link, tema, titulo, transcricao_completa, datetime.utcnow()))
            conn.commit()
    finally:
        conn.close()


def get_processed_video(link: str) -> Optional[Dict[str, Any]]:
    """Obtém detalhes e a transcrição completa de um vídeo processado pelo link."""
    if not link:
        return None
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT link_do_video, tema_busca, titulo, transcricao_completa, data_processamento
                FROM videos_processados
                WHERE link_do_video = %s;
            """, (link,))
            row = cur.fetchone()
            if not row:
                return None
            it = dict(row)
            it["data_processamento"] = (
                it["data_processamento"].isoformat() if it.get("data_processamento") else None
            )
            return it
    finally:
        conn.close()


def list_processed_videos(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Lista vídeos processados ordenados por data de processamento decrescente."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    link_do_video, 
                    tema_busca, 
                    titulo, 
                    LENGTH(COALESCE(transcricao_completa, '')) AS tamanho_transcricao,
                    LEFT(COALESCE(transcricao_completa, ''), 300) AS preview_transcricao,
                    data_processamento
                FROM videos_processados
                ORDER BY data_processamento DESC
                LIMIT %s OFFSET %s;
            """, (limit, offset))
            rows = cur.fetchall()
            results = []
            for row in rows:
                it = dict(row)
                it["data_processamento"] = (
                    it["data_processamento"].isoformat() if it.get("data_processamento") else None
                )
                results.append(it)
            return results
    finally:
        conn.close()


def get_or_create_materia(nome: Optional[str]) -> int:
    """
    Busca matéria existente por slug ou insere um novo registro de forma idempotente,
    garantindo que variações e sinônimos apontem sempre para a mesma chave primária integer.
    """
    canonical_name, slug = normalize_materia(nome)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO materias (nome, slug)
                VALUES (%s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    nome = COALESCE(materias.nome, EXCLUDED.nome)
                RETURNING id;
            """, (canonical_name, slug))
            materia_id = cur.fetchone()[0]
            conn.commit()
            return materia_id
    finally:
        conn.close()


def list_materias() -> List[Dict[str, Any]]:
    """Lista todas as matérias cadastradas com contadores de tópicos mestres e total de itens."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    m.id, 
                    m.nome, 
                    m.slug, 
                    m.criado_em,
                    COUNT(DISTINCT CASE WHEN i.parent_id IS NULL THEN i.id END) AS qtd_topicos,
                    COUNT(DISTINCT i.id) AS qtd_itens
                FROM materias m
                LEFT JOIN itens_estudo i ON i.materia_id = m.id
                GROUP BY m.id, m.nome, m.slug, m.criado_em
                ORDER BY m.nome ASC;
            """)
            rows = cur.fetchall()
            results = []
            for r in rows:
                it = dict(r)
                it["criado_em"] = it["criado_em"].isoformat() if it.get("criado_em") else None
                results.append(it)
            return results
    finally:
        conn.close()


def search_similar_items(
    embedding: List[float],
    limit: int = 3,
    categoria: Optional[str] = None,
    min_similarity: Optional[float] = None,
    materia_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Busca os itens mais semanticamente próximos usando operador <=> de pgvector com suporte a filtro por matéria."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            vector_json = json.dumps(embedding)
            
            where_clauses = []
            params = [vector_json]
            
            if categoria:
                where_clauses.append("i.categoria = %s")
                params.append(categoria)
            if materia_id is not None:
                where_clauses.append("i.materia_id = %s")
                params.append(materia_id)

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            query = f"""
                SELECT 
                    i.id, i.topico, i.categoria, i.conteudo, i.detalhes_resposta, 
                    i.fragmentos, i.criado_em, i.data_ultima_revisao, i.qtd_revisoes, 
                    i.link_do_video, i.parent_id, i.materia_id, m.nome AS materia_nome,
                    (i.embedding <=> %s::vector) AS distance
                FROM itens_estudo i
                LEFT JOIN materias m ON i.materia_id = m.id
                {where_str}
                ORDER BY i.embedding <=> %s::vector
                LIMIT %s;
            """
            params.extend([vector_json, limit])
            cur.execute(query, tuple(params))

            rows = cur.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["criado_em"] = item["criado_em"].isoformat() if item.get("criado_em") else None
                item["data_ultima_revisao"] = (
                    item["data_ultima_revisao"].isoformat() if item.get("data_ultima_revisao") else None
                )
                distance = float(item.get("distance", 0.0))
                similarity = max(0.0, round(1.0 - distance, 4))
                if min_similarity is not None and similarity < min_similarity:
                    continue
                item["similarity"] = similarity
                results.append(item)
            return results
    finally:
        conn.close()


def search_master_topic(
    embedding: List[float],
    min_similarity: float = 0.70,
    materia_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Busca o tópico mestre (parent_id IS NULL) mais próximo pelo vetor de embedding e carrega todo seu histórico.
    Se materia_id for informado, particiona o espaço de busca no pgvector evitando colisões semânticas.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            vector_json = json.dumps(embedding)
            params = [vector_json]
            materia_filter = ""
            if materia_id is not None:
                materia_filter = "AND i.materia_id = %s"
                params.append(materia_id)
            params.append(vector_json)

            query = f"""
                SELECT 
                    i.id, i.topico, i.categoria, i.conteudo, i.detalhes_resposta, 
                    i.fragmentos, i.criado_em, i.data_ultima_revisao, i.qtd_revisoes, 
                    i.link_do_video, i.parent_id, i.materia_id, m.nome AS materia_nome,
                    (i.embedding <=> %s::vector) AS distance
                FROM itens_estudo i
                LEFT JOIN materias m ON i.materia_id = m.id
                WHERE i.parent_id IS NULL {materia_filter}
                ORDER BY i.embedding <=> %s::vector
                LIMIT 1;
            """
            cur.execute(query, tuple(params))
            row = cur.fetchone()

            # Se não encontrou com filtro específico de materia_id, faz fallback para busca geral ignorando a matéria
            if not row and materia_id is not None:
                query_fallback = query.replace("AND i.materia_id = %s", "")
                cur.execute(query_fallback, (vector_json, vector_json))
                row = cur.fetchone()

            if not row:
                return None
            item = dict(row)
            distance = float(item.get("distance", 0.0))
            similarity = max(0.0, round(1.0 - distance, 4))
            if similarity < min_similarity:
                return None
            item["similarity"] = similarity
            item["criado_em"] = item["criado_em"].isoformat() if item.get("criado_em") else None
            item["data_ultima_revisao"] = (
                item["data_ultima_revisao"].isoformat() if item.get("data_ultima_revisao") else None
            )

            # Carrega filhos vinculados
            cur.execute("""
                SELECT i.id, i.topico, i.categoria, i.conteudo, i.detalhes_resposta, i.criado_em, i.link_do_video, i.parent_id, i.materia_id, m.nome AS materia_nome
                FROM itens_estudo i
                LEFT JOIN materias m ON i.materia_id = m.id
                WHERE i.parent_id = %s
                ORDER BY i.id ASC;
            """, (item["id"],))
            children = cur.fetchall()
            item["filhos"] = [
                {
                    **dict(c),
                    "criado_em": c["criado_em"].isoformat() if c.get("criado_em") else None
                }
                for c in children
            ]
            return item
    finally:
        conn.close()


def insert_new_item(
    item: ItemEstudo,
    embedding: List[float],
    link_do_video: Optional[str] = None,
    parent_id: Optional[int] = None,
    materia_id: Optional[int] = None
) -> int:
    """Insere um novo tópico base ou item filho com seu embedding e chave estrangeira materia_id."""
    conn = get_db_connection()
    now = datetime.utcnow()
    effective_parent_id = parent_id if parent_id is not None else getattr(item, 'parent_id', None)
    effective_materia_id = materia_id if materia_id is not None else getattr(item, 'materia_id', None)

    # Se ainda não temos materia_id, tenta resolver pelo nome da matéria em item
    if effective_materia_id is None and getattr(item, 'materia', None):
        try:
            effective_materia_id = get_or_create_materia(item.materia)
        except Exception as e:
            logger.warning(f"Não foi possível resolver materia_id para '{item.materia}': {e}")

    try:
        with conn.cursor() as cur:
            vector_json = json.dumps(embedding)
            cur.execute("""
                INSERT INTO itens_estudo 
                (topico, categoria, conteudo, detalhes_resposta, fragmentos, criado_em, data_ultima_revisao, qtd_revisoes, link_do_video, parent_id, materia_id, embedding)
                VALUES (%s, %s, %s, %s, '[]'::jsonb, %s, %s, 1, %s, %s, %s, %s::vector)
                RETURNING id;
            """, (
                item.topico,
                item.categoria,
                item.conteudo,
                item.detalhes_resposta,
                now,
                now,
                link_do_video,
                effective_parent_id,
                effective_materia_id,
                vector_json
            ))
            new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
    finally:
        conn.close()


def append_fragment_to_item(
    item_id: int,
    categoria: str,
    conteudo_incremental: str,
    justificativa: str,
    link_do_video: Optional[str] = None,
    topico: Optional[str] = None,
    detalhes_resposta: Optional[str] = None,
    embedding: Optional[List[float]] = None,
    materia_id: Optional[int] = None
) -> bool:
    """
    Adiciona um fragmento incremental ao array JSONB do item mestre existente E
    persiste uma linha filha vinculada (parent_id = item_id) herdando materia_id.
    """
    conn = get_db_connection()
    now = datetime.utcnow()
    fragment = {
        "topico": topico,
        "categoria": categoria,
        "conteudo_incremental": conteudo_incremental,
        "detalhes_resposta": detalhes_resposta,
        "justificativa": justificativa,
        "adicionado_em": now.isoformat(),
        "link_do_video": link_do_video
    }
    fragment_json = json.dumps([fragment])
    try:
        with conn.cursor() as cur:
            # Se materia_id não foi passado, herda do item mestre
            effective_materia_id = materia_id
            if effective_materia_id is None:
                cur.execute("SELECT materia_id FROM itens_estudo WHERE id = %s;", (item_id,))
                row = cur.fetchone()
                if row and row[0] is not None:
                    effective_materia_id = row[0]

            # 1. Anexa no JSONB do mestre
            cur.execute("""
                UPDATE itens_estudo
                SET 
                    fragmentos = COALESCE(fragmentos, '[]'::jsonb) || %s::jsonb,
                    qtd_revisoes = qtd_revisoes + 1,
                    data_ultima_revisao = %s
                WHERE id = %s;
            """, (fragment_json, now, item_id))
            updated = cur.rowcount > 0

            # 2. Persiste como linha filha relacional com parent_id (evitando duplicações idênticas)
            cur.execute("""
                SELECT id FROM itens_estudo 
                WHERE parent_id = %s AND categoria = %s AND conteudo = %s
                LIMIT 1;
            """, (item_id, categoria, conteudo_incremental))
            child_exists = cur.fetchone() is not None

            if not child_exists:
                child_topico = topico or f"Fragmento de {categoria}"
                vector_json = json.dumps(embedding) if embedding is not None else None
                cur.execute("""
                    INSERT INTO itens_estudo 
                    (topico, categoria, conteudo, detalhes_resposta, fragmentos, criado_em, data_ultima_revisao, qtd_revisoes, link_do_video, parent_id, materia_id, embedding)
                    VALUES (%s, %s, %s, %s, '[]'::jsonb, %s, %s, 1, %s, %s, %s, %s::vector);
                """, (
                    child_topico,
                    categoria,
                    conteudo_incremental,
                    detalhes_resposta,
                    now,
                    now,
                    link_do_video,
                    item_id,
                    effective_materia_id,
                    vector_json
                ))

            conn.commit()
            return updated
    finally:
        conn.close()


def list_items(
    limit: int = 50,
    offset: int = 0,
    categoria: Optional[str] = None,
    search: Optional[str] = None,
    only_parents: bool = True,
    materia_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Lista os tópicos cadastrados com contadores, filhos organizados e informações da matéria."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT 
                    i.id, i.topico, i.categoria, i.conteudo, i.detalhes_resposta,
                    i.fragmentos, i.parent_id, i.materia_id, m.nome AS materia_nome,
                    i.criado_em, i.data_ultima_revisao, i.qtd_revisoes, i.link_do_video
                FROM itens_estudo i
                LEFT JOIN materias m ON i.materia_id = m.id
                WHERE 1=1
            """
            params: List[Any] = []
            if only_parents:
                query += " AND i.parent_id IS NULL"
            if categoria:
                query += " AND i.categoria = %s"
                params.append(categoria)
            if materia_id is not None:
                query += " AND i.materia_id = %s"
                params.append(materia_id)
            if search:
                query += " AND (i.topico ILIKE %s OR i.conteudo ILIKE %s OR m.nome ILIKE %s)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

            query += " ORDER BY i.data_ultima_revisao DESC LIMIT %s OFFSET %s;"
            params.extend([limit, offset])

            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            items = []
            master_ids = []
            for row in rows:
                it = dict(row)
                it["criado_em"] = it["criado_em"].isoformat() if it.get("criado_em") else None
                it["data_ultima_revisao"] = (
                    it["data_ultima_revisao"].isoformat() if it.get("data_ultima_revisao") else None
                )
                items.append(it)
                master_ids.append(it["id"])

            if master_ids:
                cur.execute("""
                    SELECT 
                        i.id, i.topico, i.categoria, i.conteudo, i.detalhes_resposta, 
                        i.criado_em, i.link_do_video, i.parent_id, i.materia_id, m.nome AS materia_nome
                    FROM itens_estudo i
                    LEFT JOIN materias m ON i.materia_id = m.id
                    WHERE i.parent_id = ANY(%s)
                    ORDER BY i.id ASC;
                """, (master_ids,))
                children_rows = cur.fetchall()
                children_by_parent: Dict[int, List[Dict[str, Any]]] = {mid: [] for mid in master_ids}
                for c in children_rows:
                    c_dict = dict(c)
                    c_dict["criado_em"] = c_dict["criado_em"].isoformat() if c_dict.get("criado_em") else None
                    children_by_parent[c["parent_id"]].append(c_dict)

                for it in items:
                    it["filhos"] = children_by_parent.get(it["id"], [])
                    # Calcula contadores consolidados a partir da base relacional (evitando dupla contagem com JSONB)
                    filhos_list = it["filhos"]
                    it["qtd_questoes"] = sum(1 for c in filhos_list if c["categoria"] == "questao")
                    it["qtd_pegadinhas"] = sum(1 for c in filhos_list if c["categoria"] == "pegadinha")
                    it["qtd_sacadas"] = sum(1 for c in filhos_list if c["categoria"] == "sacada")
            else:
                for it in items:
                    it["filhos"] = []
                    it["qtd_questoes"] = 0
                    it["qtd_pegadinhas"] = 0
                    it["qtd_sacadas"] = 0

            return items
    finally:
        conn.close()


def get_item_by_id(item_id: int) -> Optional[Dict[str, Any]]:
    """Obtém os detalhes de um item específico por ID, incluindo matéria, fragmentos e filhos vinculados."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    i.id, i.topico, i.categoria, i.conteudo, i.detalhes_resposta,
                    i.fragmentos, i.parent_id, i.materia_id, m.nome AS materia_nome,
                    i.criado_em, i.data_ultima_revisao, i.qtd_revisoes, i.link_do_video
                FROM itens_estudo i
                LEFT JOIN materias m ON i.materia_id = m.id
                WHERE i.id = %s;
            """, (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            it = dict(row)
            it["criado_em"] = it["criado_em"].isoformat() if it.get("criado_em") else None
            it["data_ultima_revisao"] = (
                it["data_ultima_revisao"].isoformat() if it.get("data_ultima_revisao") else None
            )

            # Busca filhos vinculados via parent_id
            cur.execute("""
                SELECT 
                    i.id, i.topico, i.categoria, i.conteudo, i.detalhes_resposta,
                    i.criado_em, i.link_do_video, i.parent_id, i.materia_id, m.nome AS materia_nome
                FROM itens_estudo i
                LEFT JOIN materias m ON i.materia_id = m.id
                WHERE i.parent_id = %s
                ORDER BY i.id ASC;
            """, (item_id,))
            children = cur.fetchall()
            it["filhos"] = [
                {
                    **dict(c),
                    "criado_em": c["criado_em"].isoformat() if c.get("criado_em") else None
                }
                for c in children
            ]
            # Calcula contadores consolidados a partir da base relacional
            it["qtd_questoes"] = sum(1 for c in it["filhos"] if c["categoria"] == "questao")
            it["qtd_pegadinhas"] = sum(1 for c in it["filhos"] if c["categoria"] == "pegadinha")
            it["qtd_sacadas"] = sum(1 for c in it["filhos"] if c["categoria"] == "sacada")
            return it
    finally:
        conn.close()


def get_stats() -> Dict[str, Any]:
    """Retorna estatísticas gerais da base de conhecimento com distinção de tópicos mestres e total de matérias."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Tópicos mestres (parent_id IS NULL)
            cur.execute("SELECT COUNT(*) FROM itens_estudo WHERE parent_id IS NULL;")
            total_itens = cur.fetchone()[0]

            # Total vídeos processados
            cur.execute("SELECT COUNT(*) FROM videos_processados;")
            total_videos = cur.fetchone()[0]

            # Total fragmentos incrementais: itens filhos (parent_id IS NOT NULL) + fragmentos em JSONB
            cur.execute("""
                SELECT 
                    (SELECT COUNT(*) FROM itens_estudo WHERE parent_id IS NOT NULL) +
                    (SELECT COALESCE(SUM(jsonb_array_length(fragmentos)), 0) FROM itens_estudo WHERE parent_id IS NULL);
            """)
            total_fragmentos = cur.fetchone()[0]

            # Total de matérias cadastradas
            cur.execute("SELECT COUNT(*) FROM materias;")
            total_materias = cur.fetchone()[0]

            # Contagem por categoria
            cur.execute("""
                SELECT categoria, COUNT(*) 
                FROM itens_estudo 
                GROUP BY categoria;
            """)
            categorias = dict(cur.fetchall())

            return {
                "total_itens": total_itens,
                "total_videos": total_videos,
                "total_fragmentos": int(total_fragmentos),
                "total_materias": total_materias,
                "categorias": categorias,
            }
    finally:
        conn.close()
