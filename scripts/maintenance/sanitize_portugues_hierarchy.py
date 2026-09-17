import json
import logging
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sanitize_portugues_hierarchy")

CLUSTERS = [
    {
        "nome": "Crase",
        "master_id": 1,
        "children_ids": [2, 3, 4, 5, 6, 7],
    },
    {
        "nome": "Regência na reescrita com pronome relativo",
        "master_id": 8,
        "children_ids": [9, 10, 11],
    },
    {
        "nome": "Orações Subordinadas Substantivas",
        "master_id": 13,
        "children_ids": [12, 14, 15, 16],
    },
    {
        "nome": "Sintaxe - Funções Sintáticas e Coesão",
        "master_id": 18,
        "children_ids": [17, 19, 20, 21],
    },
    {
        "nome": "Reescrita de Frases",
        "master_id": 22,
        "children_ids": [23, 24, 25, 26, 27, 28, 29],
    },
    {
        "nome": "Coerência textual e supressão de palavras",
        "master_id": 30,
        "children_ids": [31, 36, 37],
    },
    {
        "nome": "Sintaxe - Identificação de Sujeito e Complementos",
        "master_id": 32,
        "children_ids": [33, 35, 38, 40],
    },
    {
        "nome": "Colocação pronominal",
        "master_id": 34,
        "children_ids": [39],
    },
]


def sanitize_portugues():
    """
    Saneia os registros de Língua Portuguesa (IDs 1 a 40):
    - Promove os verdadeiros núcleos conceituais a Tópicos Mestres (parent_id = NULL).
    - Vincula os respectivos subitens (questões, sacadas, pegadinhas) aos seus mestres corretos.
    - Reconstrói os arrays JSONB 'fragmentos' isolados para cada mestre, eliminando contaminação cruzada.
    """
    conn = psycopg2.connect(**settings.db_params)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for cluster in CLUSTERS:
                m_id = cluster["master_id"]
                c_ids = cluster["children_ids"]
                nome = cluster["nome"]

                logger.info(f"Processando núcleo '{nome}' (Mestre #{m_id}, {len(c_ids)} filhos)...")

                # 1. Promove mestre (parent_id = NULL)
                cur.execute("""
                    UPDATE itens_estudo
                    SET parent_id = NULL
                    WHERE id = %s;
                """, (m_id,))

                # 2. Vincula filhos
                if c_ids:
                    cur.execute("""
                        UPDATE itens_estudo
                        SET parent_id = %s
                        WHERE id = ANY(%s);
                    """, (m_id, c_ids))

                    # 3. Carrega os dados dos filhos para montar o JSONB limpo do mestre
                    cur.execute("""
                        SELECT id, topico, categoria, conteudo, gabarito, detalhes_resposta, criado_em, link_do_video
                        FROM itens_estudo
                        WHERE id = ANY(%s)
                        ORDER BY id ASC;
                    """, (c_ids,))
                    child_rows = cur.fetchall()

                    fragments = []
                    for r in child_rows:
                        fragments.append({
                            "id_origem": r["id"],
                            "topico": r["topico"],
                            "categoria": r["categoria"],
                            "conteudo_incremental": r["conteudo"],
                            "gabarito": r.get("gabarito"),
                            "detalhes_resposta": r.get("detalhes_resposta"),
                            "justificativa": f"Item vinculado ao tópico mestre #{m_id} ({nome})",
                            "adicionado_em": r["criado_em"].isoformat() if r.get("criado_em") else datetime.utcnow().isoformat(),
                            "link_do_video": r.get("link_do_video")
                        })

                    cur.execute("""
                        UPDATE itens_estudo
                        SET 
                            fragmentos = %s::jsonb,
                            qtd_revisoes = %s,
                            data_ultima_revisao = %s
                        WHERE id = %s;
                    """, (json.dumps(fragments), len(fragments) + 1, datetime.utcnow(), m_id))

            conn.commit()
            logger.info("Saneamento de Língua Portuguesa concluído com sucesso!")
    finally:
        conn.close()


if __name__ == "__main__":
    sanitize_portugues()
