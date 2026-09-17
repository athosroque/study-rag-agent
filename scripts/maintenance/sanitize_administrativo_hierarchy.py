import json
import logging
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sanitize_administrativo_hierarchy")

CLUSTERS = [
    {
        "nome": "Agentes Públicos: Conceito, Classificação e Ingresso",
        "master_id": 43,
        "children_ids": list(range(44, 136)) + [240, 241, 243],
    },
    {
        "nome": "Direito à Nomeação, Concurso e Cargos em Comissão",
        "master_id": 136,
        "children_ids": list(range(137, 158)),
    },
    {
        "nome": "Direito de Associação Sindical e Greve",
        "master_id": 158,
        "children_ids": list(range(159, 166)),
    },
    {
        "nome": "Reserva de Vagas para Pessoas com Deficiência (PcD)",
        "master_id": 166,
        "children_ids": list(range(167, 174)),
    },
    {
        "nome": "Contratação de Temporários (art. 37, IX, CF)",
        "master_id": 174,
        "children_ids": list(range(175, 180)),
    },
    {
        "nome": "Acumulação de Cargos e Mandatos Eletivos",
        "master_id": 180,
        "children_ids": list(range(181, 206)) + [242],
    },
    {
        "nome": "Estabilidade no Serviço Público e Perda do Cargo",
        "master_id": 206,
        "children_ids": list(range(207, 226)),
    },
    {
        "nome": "Vitaliciedade, Reintegração e Disponibilidade",
        "master_id": 226,
        "children_ids": list(range(227, 231)),
    },
    {
        "nome": "Regimes de Previdência (RPPS) e Aposentadoria",
        "master_id": 231,
        "children_ids": list(range(232, 240)),
    },
]


def sanitize_administrativo():
    """
    Saneia os registros de Direito Administrativo (IDs 43 a 243):
    - Promove os verdadeiros núcleos conceituais a Tópicos Mestres (parent_id = NULL).
    - Vincula os respectivos subitens (questões, sacadas, pegadinhas) aos seus mestres corretos.
    - Reconstrói os arrays JSONB 'fragmentos' isolados para cada mestre, eliminando sobrecarga no ID 43.
    """
    conn = psycopg2.connect(**settings.db_params)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            total_processado = 0
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

                total_processado += 1 + len(c_ids)

            conn.commit()
            logger.info(f"Saneamento de Direito Administrativo concluído com sucesso! {total_processado} itens organizados em {len(CLUSTERS)} tópicos mestres.")
    finally:
        conn.close()


if __name__ == "__main__":
    sanitize_administrativo()
