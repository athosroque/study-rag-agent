#!/usr/bin/env python3
"""
Script para separar questões que tiveram enunciado e resposta concatenados em 'conteudo'.

Problema corrigido:
  Em alguns itens legados extraídos por LLM, o campo 'conteudo' continha tanto a pergunta
  quanto a resposta (ex: "Pergunta: ... Resposta: ..."), enquanto 'detalhes_resposta'
  continha apenas uma meta-justificativa genérica (ex: "Questão chave de fixação...").
  
Ação deste script:
  1. Detecta o padrão 'Resposta:' ou 'Gabarito:' dentro de 'conteudo'.
  2. Isola o enunciado no campo 'conteudo'.
  3. Move a resposta real para o campo 'detalhes_resposta'.
  4. Preserva observações relevantes anteriores se agregarem valor.
"""
import json
import logging
import os
import re
import sys

# Garante raiz do projeto no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from psycopg2.extras import RealDictCursor
from src import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("split_questions")


def split_merged_questions() -> dict:
    conn = db.get_db_connection()
    alterados = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, topico, categoria, conteudo, detalhes_resposta
                FROM itens_estudo
                WHERE categoria = 'questao'
                ORDER BY id ASC;
            """)
            rows = cur.fetchall()

            for r in rows:
                c = r["conteudo"]
                old_d = r.get("detalhes_resposta") or ""

                m = re.search(r'(.*?)(?:\s*(?:Resposta|Gabarito):\s*)(.*)', c, re.DOTALL | re.IGNORECASE)
                if not m:
                    continue

                enunciado = m.group(1).strip()
                resposta_extraida = m.group(2).strip()

                # Limpa prefixos desnecessários do enunciado se existirem
                # (ex: "Questão: '...'" -> "'...'")
                # mas mantém o enunciado completo legível
                if not enunciado or len(enunciado) < 5:
                    continue

                # Avalia se a observação antiga tem valor ou é apenas meta-descrição
                meta_patterns = [
                    "questão chave", "questão clássica", "pergunta direta",
                    "questão de fixação", "questão prática", "exercício de fixação"
                ]
                eh_meta = any(p in old_d.lower() for p in meta_patterns)

                if not old_d or eh_meta or len(old_d) < 5:
                    novo_detalhes = resposta_extraida
                else:
                    # Se old_d for algo como "Letra D" ou uma justificativa curta já existente,
                    # combina com a resposta detalhada extraída
                    if old_d.lower() not in resposta_extraida.lower():
                        novo_detalhes = f"{resposta_extraida}\n\n💡 *Gabarito complementar:* {old_d}"
                    else:
                        novo_detalhes = resposta_extraida

                # Atualiza no PostgreSQL
                cur.execute("""
                    UPDATE itens_estudo
                    SET conteudo = %s, detalhes_resposta = %s
                    WHERE id = %s;
                """, (enunciado, novo_detalhes, r["id"]))

                alterados.append({
                    "id": r["id"],
                    "topico": r["topico"],
                    "enunciado_novo": enunciado,
                    "detalhes_novo": novo_detalhes,
                })
                logger.info(f"Item #{r['id']} atualizado com sucesso. Enunciado: {len(enunciado)} chars | Gabarito: {len(novo_detalhes)} chars.")

            conn.commit()

        return {
            "total_verificados": len(rows),
            "total_corrigidos": len(alterados),
            "itens_corrigidos": [a["id"] for a in alterados]
        }
    finally:
        conn.close()


def main() -> int:
    resultado = split_merged_questions()
    print("\n--- Resultado da Separação de Enunciados e Gabaritos ---")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
