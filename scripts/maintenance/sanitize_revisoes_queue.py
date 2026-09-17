#!/usr/bin/env python3
"""
Script de saneamento da fila de repetição espaçada (tabela 'revisoes').

Regra de negócio:
  - O ciclo de revisão ativa (Ebbinghaus) no Telegram é exclusivo para QUESTÕES ('questao').
  - Teorias, sacadas e pegadinhas devem ser consultadas sob demanda e não devem
    poluir o cronograma ativo de exercícios.
  - Este script remove da tabela 'revisoes' todos os agendamentos vinculados a itens
    que não sejam do tipo 'questao' com enunciado e gabarito válidos.
"""
import json
import logging
import os
import sys

# Garante raiz do projeto no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from psycopg2.extras import RealDictCursor
from src import db
from src import revisoes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sanitize_revisoes")


def sanitize_queue() -> dict:
    conn = db.get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Diagnóstico inicial
            cur.execute("""
                SELECT i.categoria, COUNT(*) AS qtd
                FROM revisoes r
                JOIN itens_estudo i ON i.id = r.item_id
                GROUP BY i.categoria
                ORDER BY qtd DESC;
            """)
            antes = {r["categoria"]: r["qtd"] for r in cur.fetchall()}
            logger.info(f"Distribuição antes do saneamento: {antes}")

            # 2. Remoção de itens cuja categoria != 'questao'
            cur.execute("""
                DELETE FROM revisoes
                WHERE item_id IN (
                    SELECT id FROM itens_estudo WHERE categoria != 'questao'
                );
            """)
            removidos_categoria = cur.rowcount
            logger.info(f"Removidas {removidos_categoria} revisões de categorias não-questão.")

            # 3. Remoção de questões inválidas (sem enunciado ou sem gabarito)
            cur.execute("""
                DELETE FROM revisoes
                WHERE item_id IN (
                    SELECT id FROM itens_estudo
                    WHERE categoria = 'questao'
                      AND (
                        conteudo IS NULL OR LENGTH(TRIM(conteudo)) < 5
                        OR detalhes_resposta IS NULL OR LENGTH(TRIM(detalhes_resposta)) < 2
                      )
                );
            """)
            removidos_invalidos = cur.rowcount
            if removidos_invalidos > 0:
                logger.warning(f"Removidas {removidos_invalidos} questões com enunciado ou gabarito inválido.")

            conn.commit()

            # 4. Diagnóstico final
            cur.execute("""
                SELECT i.categoria, COUNT(*) AS qtd
                FROM revisoes r
                JOIN itens_estudo i ON i.id = r.item_id
                GROUP BY i.categoria;
            """)
            depois = {r["categoria"]: r["qtd"] for r in cur.fetchall()}
            logger.info(f"Distribuição após o saneamento: {depois}")

            return {
                "antes": antes,
                "removidos_nao_questao": removidos_categoria,
                "removidos_invalidos": removidos_invalidos,
                "depois": depois,
                "total_questoes_ativas": depois.get("questao", 0)
            }
    finally:
        conn.close()


def main() -> int:
    resultado = sanitize_queue()
    print("\n--- Relatório de Saneamento da Fila de Revisões ---")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    
    stats = revisoes.estatisticas(categoria="questao")
    print("\n--- Novo Estado do Ciclo (Apenas Questões) ---")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
