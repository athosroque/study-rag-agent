import json
import logging
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sanitize_hierarchy")


def sanitize_rlm_hierarchy(master_id: int = 4):
    """
    Saneia a aula de RLM (registros 4 a 25):
    - ID 4 torna-se o Tópico Mestre ('Conceito de Tabela Verdade e Equivalências', parent_id = NULL)
    - IDs 5 a 25 recebem parent_id = 4
    - Fragmentos dos registros 5 a 25 são adicionados ao array JSONB do ID 4
    """
    conn = psycopg2.connect(**settings.db_params)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Verifica se o registro mestre ID 4 existe
            cur.execute("SELECT * FROM itens_estudo WHERE id = %s;", (master_id,))
            master = cur.fetchone()
            if not master:
                logger.warning(f"Registro mestre #{master_id} não encontrado. Abortando saneamento.")
                return

            logger.info(f"Registro mestre #{master_id} encontrado: '{master['topico']}'. Atualizando título e parent_id...")
            cur.execute("""
                UPDATE itens_estudo
                SET 
                    topico = 'Conceito de Tabela Verdade e Equivalências',
                    parent_id = NULL
                WHERE id = %s;
            """, (master_id,))

            # 2. Busca registros filhos legados (IDs 5 a 25)
            cur.execute("""
                SELECT id, topico, categoria, conteudo, detalhes_resposta, criado_em, link_do_video
                FROM itens_estudo
                WHERE id BETWEEN 5 AND 25
                ORDER BY id ASC;
            """)
            child_rows = cur.fetchall()
            logger.info(f"Encontrados {len(child_rows)} registros para vinculação como filhos de #{master_id}.")

            # 3. Atualiza parent_id = 4 nos registros filhos
            cur.execute("""
                UPDATE itens_estudo
                SET parent_id = %s
                WHERE id BETWEEN 5 AND 25;
            """, (master_id,))

            # 4. Agrega ao array JSONB do mestre se ainda não estiver presente
            existing_frags = master.get("fragmentos") or []
            existing_contents = {f.get("conteudo_incremental") for f in existing_frags if isinstance(f, dict)}

            new_frags = []
            for r in child_rows:
                if r["conteudo"] not in existing_contents:
                    new_frags.append({
                        "id_origem": r["id"],
                        "topico": r["topico"],
                        "categoria": r["categoria"],
                        "conteudo_incremental": r["conteudo"],
                        "detalhes_resposta": r["detalhes_resposta"],
                        "justificativa": f"Item pedagógico vinculado hierarquicamente ao tópico mestre #{master_id}",
                        "adicionado_em": r["criado_em"].isoformat() if r.get("criado_em") else datetime.utcnow().isoformat(),
                        "link_do_video": r["link_do_video"]
                    })
                    existing_contents.add(r["conteudo"])

            if new_frags:
                cur.execute("""
                    UPDATE itens_estudo
                    SET 
                        fragmentos = COALESCE(fragmentos, '[]'::jsonb) || %s::jsonb,
                        qtd_revisoes = %s,
                        data_ultima_revisao = %s
                    WHERE id = %s;
                """, (json.dumps(new_frags), len(child_rows) + 1, datetime.utcnow(), master_id))
                logger.info(f"{len(new_frags)} fragmentos agregados ao JSONB do mestre #{master_id}.")

            conn.commit()
            logger.info(f"Saneamento concluído com sucesso para o Tópico Mestre #{master_id} e seus {len(child_rows)} filhos.")
    finally:
        conn.close()


if __name__ == "__main__":
    sanitize_rlm_hierarchy(4)
