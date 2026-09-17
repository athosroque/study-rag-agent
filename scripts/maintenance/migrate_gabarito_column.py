"""
Script de Manutenção: Migração da coluna 'gabarito' em 'itens_estudo'

1. Garante que a coluna 'gabarito TEXT' exista na tabela itens_estudo.
2. Para questões CESPE (com **Gabarito:** [CERTO/ERRADO] em detalhes_resposta):
   - Extrai o gabarito oficial ('CERTO' ou 'ERRADO') para a coluna 'gabarito'.
   - Mantém as justificativas e pegadinhas em 'detalhes_resposta'.
3. Para questões com respostas diretas/discursivas (ex: questão #7 de Crase):
   - Popula a coluna 'gabarito' com a resposta.
4. Atualiza os fragmentos no JSONB para consistência.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import re
import json
import logging
from psycopg2.extras import RealDictCursor
from src.db import get_db_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_gabarito")


def run_migration():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Garante a existência da coluna
            logger.info("Adicionando coluna 'gabarito TEXT' caso não exista...")
            cur.execute("ALTER TABLE itens_estudo ADD COLUMN IF NOT EXISTS gabarito TEXT;")
            conn.commit()

            # 2. Carrega todas as questões existentes
            cur.execute("""
                SELECT id, topico, conteudo, gabarito, detalhes_resposta, fragmentos
                FROM itens_estudo
                WHERE categoria = 'questao'
                ORDER BY id ASC;
            """)
            questions = cur.fetchall()
            logger.info(f"Total de questões a avaliar: {len(questions)}")

            migrados_cespe = 0
            migrados_diretos = 0
            inalterados = 0

            cespe_pattern = re.compile(r'\*\*Gabarito:\*\*\s*\[?(CERTO|ERRADO)\]?', re.IGNORECASE)

            for q in questions:
                item_id = q["id"]
                det = (q.get("detalhes_resposta") or "").strip()
                current_gab = (q.get("gabarito") or "").strip()

                new_gab = None

                # Tenta padrão CESPE
                m = cespe_pattern.search(det)
                if m:
                    new_gab = m.group(1).upper()
                    migrados_cespe += 1
                elif det and not current_gab:
                    # Resposta direta (ex: questão #7)
                    new_gab = det
                    migrados_diretos += 1
                elif current_gab:
                    inalterados += 1
                    continue
                else:
                    inalterados += 1
                    continue

                # Atualiza o registro
                cur.execute("""
                    UPDATE itens_estudo
                    SET gabarito = %s
                    WHERE id = %s;
                """, (new_gab, item_id))

            conn.commit()
            logger.info(
                f"Migração concluída: {migrados_cespe} questões CESPE normalizadas, "
                f"{migrados_diretos} respostas diretas migradas para gabarito, "
                f"{inalterados} inalteradas."
            )

            # 3. Atualiza fragmentos JSONB nos tópicos mestres
            logger.info("Verificando fragmentos JSONB nos tópicos mestres...")
            cur.execute("SELECT id, fragmentos FROM itens_estudo WHERE fragmentos IS NOT NULL AND jsonb_array_length(fragmentos) > 0;")
            mestres = cur.fetchall()
            frags_atualizados = 0

            for m_row in mestres:
                m_id = m_row["id"]
                frags = m_row.get("fragmentos") or []
                modificado = False
                for frag in frags:
                    if frag.get("categoria") == "questao" and not frag.get("gabarito"):
                        f_det = frag.get("detalhes_resposta") or ""
                        m_cespe = cespe_pattern.search(f_det)
                        if m_cespe:
                            frag["gabarito"] = m_cespe.group(1).upper()
                            modificado = True
                        elif f_det:
                            frag["gabarito"] = f_det
                            modificado = True

                if modificado:
                    cur.execute("""
                        UPDATE itens_estudo
                        SET fragmentos = %s::jsonb
                        WHERE id = %s;
                    """, (json.dumps(frags), m_id))
                    frags_atualizados += 1

            conn.commit()
            logger.info(f"Tópicos mestres com fragmentos JSONB atualizados: {frags_atualizados}")

            # 4. Exibe status do item #7 de Crase
            cur.execute("SELECT id, topico, categoria, conteudo, gabarito, detalhes_resposta FROM itens_estudo WHERE id = 7;")
            item7 = cur.fetchone()
            if item7:
                logger.info("=== Estado do Item #7 após migração ===")
                logger.info(f"ID: {item7['id']}")
                logger.info(f"Tópico: {item7['topico']}")
                logger.info(f"Conteúdo (Enunciado): {item7['conteudo']}")
                logger.info(f"Gabarito: {item7['gabarito']}")
                logger.info(f"Detalhes: {item7['detalhes_resposta']}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
