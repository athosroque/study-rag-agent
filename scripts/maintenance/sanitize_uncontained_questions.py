"""
Script de Manutenção: Saneamento de Questões com Enunciados Truncados / Dependentes de Texto

Identifica questões que fazem menção a 'do texto', 'no texto' ou referências cegas
sem fornecer o contexto/oração correspondente, e aplica o saneamento do Item #39
(Colocação pronominal: 'os cerca').
"""
import os
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from psycopg2.extras import RealDictCursor
from src.db import get_db_connection
from src.embeddings import embedding_manager
from src.revisoes import verificar_autossuficiencia_enunciado

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sanitize_questions")


NOVO_CONTEUDO_39 = (
    "Considere uma oração hipotética em que haja uma palavra atrativa invariável "
    "(como advérbio ou pronome indefinido) antecedendo a estrutura verbal: '... [termo invariável] os cerca ...'. "
    "Nesse contexto, estaria mantida a correção gramatical caso o pronome fosse empregado em posição enclítica ('cerca-os')?"
)
NOVO_GABARITO_39 = "ERRADO"
NOVOS_DETALHES_39 = (
    "A presença de palavra atrativa invariável (como advérbio, conjunção subordinativa ou pronome indefinido) "
    "atrai obrigatoriamente o pronome oblíquo átono para antes do verbo (próclise obrigatória: 'os cerca'). "
    "Portanto, a substituição por ênclise ('cerca-os') viola a norma culta e torna o item ERRADO."
)


def sanitize():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Carrega todas as questões
            cur.execute("""
                SELECT id, topico, conteudo, gabarito, detalhes_resposta, parent_id
                FROM itens_estudo
                WHERE categoria = 'questao'
                ORDER BY id ASC;
            """)
            questions = cur.fetchall()
            logger.info(f"Total de questões avaliadas: {len(questions)}")

            itens_invalidos = []
            for q in questions:
                ok, motivo = verificar_autossuficiencia_enunciado(q["conteudo"])
                if not ok:
                    itens_invalidos.append((q["id"], q["topico"], motivo, q["conteudo"][:80]))

            logger.info(f"Questões identificadas com vício de autossuficiência: {len(itens_invalidos)}")
            for item_id, topico, motivo, trecho in itens_invalidos:
                logger.warning(f"  - ID {item_id} [{topico}]: {motivo} | Snippet: '{trecho}...'")

            # 2. Corrige o item 39
            cur.execute("SELECT id, topico, conteudo, parent_id FROM itens_estudo WHERE id = 39;")
            item_39 = cur.fetchone()
            if item_39:
                logger.info("Atualizando item #39 com enunciado autossuficiente e gabarito 'ERRADO'...")
                texto_vetor = f"Tópico: {item_39['topico']} - {NOVO_CONTEUDO_39}"
                novo_vetor = embedding_manager.embed_query(texto_vetor)

                cur.execute("""
                    UPDATE itens_estudo
                    SET conteudo = %s,
                        gabarito = %s,
                        detalhes_resposta = %s,
                        embedding = %s
                    WHERE id = 39;
                """, (NOVO_CONTEUDO_39, NOVO_GABARITO_39, NOVOS_DETALHES_39, novo_vetor))

                # 3. Atualiza fragmentos no tópico mestre (id 34 ou parent_id)
                master_id = item_39.get("parent_id") or 34
                cur.execute("SELECT id, fragmentos FROM itens_estudo WHERE id = %s;", (master_id,))
                mestre = cur.fetchone()
                if mestre and mestre.get("fragmentos"):
                    frags = mestre["fragmentos"]
                    atualizado = False
                    for f in frags:
                        if f.get("id_origem") == 39 or "os cerca" in f.get("conteudo_incremental", "").lower():
                            f["conteudo_incremental"] = NOVO_CONTEUDO_39
                            f["gabarito"] = NOVO_GABARITO_39
                            f["detalhes_resposta"] = NOVOS_DETALHES_39
                            atualizado = True
                    if atualizado:
                        logger.info(f"Atualizando fragmentos no Tópico Mestre #{master_id}...")
                        cur.execute("""
                            UPDATE itens_estudo
                            SET fragmentos = %s::jsonb
                            WHERE id = %s;
                        """, (json.dumps(frags, ensure_ascii=False), master_id))

            conn.commit()
            logger.info("Saneamento concluído com sucesso!")

    finally:
        conn.close()


if __name__ == "__main__":
    sanitize()
