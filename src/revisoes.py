"""
Motor de Revisão Espaçada baseado na Curva do Esquecimento de Hermann Ebbinghaus.

Regras do ciclo:
  - Cada item de estudo (teoria/sacada/pegadinha/questao) entra na escada de intervalos.
  - Escada padrão (dias): 1, 2, 4, 7, 15, 30, 60, 120 (configurável via REVISAO_INTERVALOS_DIAS).
  - Autoavaliação do desempenho (0 a 3):
      0 = não lembrei / errei  -> volta para a etapa 0 (recomeça o ciclo)
      1 = lembrei com dificuldade -> repete a mesma etapa (intervalo não avança)
      2 = acertei              -> avança uma etapa
      3 = fácil / domino       -> avança uma etapa
  - Se o usuário não fez a revisão, ela é adiada para o dia seguinte (mantendo a etapa).
  - Ao esgotar a escada, o ciclo é considerado concluído.
"""
import logging
from datetime import datetime, timedelta, time as dtime
from typing import List, Dict, Any, Optional, Sequence

from psycopg2.extras import RealDictCursor

from src.config import settings
from src import db

logger = logging.getLogger(__name__)

DEFAULT_INTERVALOS: List[int] = [1, 7, 30]

STATUS_PENDENTE = "pendente"
STATUS_FEITA = "feita"
STATUS_ADIADA = "adiada"
STATUS_CONCLUIDA = "concluida"

STATUS_ABERTOS = (STATUS_PENDENTE, STATUS_ADIADA)


# =====================================================================
# UTILITÁRIOS DE TEMPO
# =====================================================================

def intervalos() -> List[int]:
    """Lê a escada de intervalos (em dias) da configuração, com fallback seguro."""
    raw = getattr(settings, "REVISAO_INTERVALOS_DIAS", "") or ""
    try:
        vals = sorted({int(x.strip()) for x in str(raw).split(",") if x.strip() and int(x.strip()) > 0})
        return vals or list(DEFAULT_INTERVALOS)
    except Exception:
        return list(DEFAULT_INTERVALOS)


def agora_local() -> datetime:
    """Retorna o 'agora' no fuso local configurado (naive, consistente com o restante do projeto)."""
    return datetime.utcnow() + timedelta(hours=settings.REVISAO_TZ_OFFSET_HORAS)


def _data_alvo(base: datetime, dias: int) -> datetime:
    """Retorna a data-calendário (base + dias) às REVISAO_HORA_AGENDAMENTO horas locais."""
    alvo = (base + timedelta(days=dias)).date()
    hora = int(getattr(settings, "REVISAO_HORA_AGENDAMENTO", 8))
    return datetime.combine(alvo, dtime(hour=max(0, min(hora, 23)), minute=0))


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else (value or None)


# =====================================================================
# AGENDAMENTO
# =====================================================================

def agendar_primeira_revisao(
    item_id: int,
    base_date: Optional[datetime] = None,
    substituir_concluidas: bool = False
) -> Optional[int]:
    """
    Cria a primeira revisão (etapa 0) de um item, se ele ainda não tiver ciclo aberto.
    Retorna o ID da revisão criada ou None se já existia.
    """
    ladder = intervalos()
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            if substituir_concluidas:
                cur.execute(
                    "SELECT 1 FROM revisoes WHERE item_id = %s AND status = ANY(%s) LIMIT 1;",
                    (item_id, list(STATUS_ABERTOS)),
                )
            else:
                cur.execute("SELECT 1 FROM revisoes WHERE item_id = %s LIMIT 1;", (item_id,))
            if cur.fetchone():
                return None

            base = base_date or agora_local()
            data_agendada = _data_alvo(base, ladder[0])
            cur.execute(
                """
                INSERT INTO revisoes (item_id, etapa, intervalo_dias, data_agendada, status, criado_em)
                VALUES (%s, 0, %s, %s, %s, %s)
                RETURNING id;
                """,
                (item_id, ladder[0], data_agendada, STATUS_PENDENTE, agora_local()),
            )
            revisao_id = cur.fetchone()[0]
            conn.commit()
            logger.info(
                f"[revisoes] Primeira revisão agendada: item {item_id} -> {data_agendada.isoformat()} "
                f"(+{ladder[0]}d)"
            )
            return revisao_id
    finally:
        conn.close()


def backfill_revisoes(
    categorias: Optional[Sequence[str]] = None,
    base_date: Optional[datetime] = None
) -> Dict[str, int]:
    """
    Cria a primeira revisão para todo item de estudo que ainda não possui nenhuma revisão.
    Usa como base a data da última revisão do item (ou a data de criação), para que a escada
    comece do momento em que o conteúdo foi estudado.
    """
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT i.id, COALESCE(i.data_ultima_revisao, i.criado_em) AS base
                FROM itens_estudo i
                WHERE NOT EXISTS (SELECT 1 FROM revisoes r WHERE r.item_id = i.id)
            """
            params: List[Any] = []
            if categorias:
                query += " AND i.categoria = ANY(%s)"
                params.append(list(categorias))
            query += " ORDER BY i.id ASC;"
            cur.execute(query, tuple(params))
            pendentes = cur.fetchall()
    finally:
        conn.close()

    criadas = 0
    for item_id, base in pendentes:
        if agendar_primeira_revisao(item_id, base_date=base_date or base):
            criadas += 1

    resultado = {
        "itens_sem_revisao": len(pendentes),
        "revisoes_criadas": criadas,
        "escada_dias": len(intervalos()),
    }
    logger.info(f"[revisoes] Backfill: {resultado}")
    return resultado


# =====================================================================
# CONSULTAS
# =====================================================================

def _serializar_revisao(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    agora = agora_local()
    agendada = item.get("data_agendada")
    atraso = (agora.date() - agendada.date()).days if isinstance(agendada, datetime) else 0
    item["data_agendada"] = _iso(agendada)
    item["data_realizada"] = _iso(item.get("data_realizada"))
    item["criado_em"] = _iso(item.get("criado_em"))
    item["atrasada_dias"] = max(0, atraso)
    item["atrasada"] = atraso > 0
    item["vence_hoje"] = atraso == 0
    return item


def listar_pendentes(
    dias: int = 0,
    materia_id: Optional[int] = None,
    categoria: Optional[str] = None,
    limite: int = 50
) -> List[Dict[str, Any]]:
    """
    Lista revisões em aberto com vencimento até hoje + `dias`.
    dias=0 -> só o que vence hoje ou já está atrasado.
    """
    limite_data = agora_local() + timedelta(days=max(0, dias))
    where = ["r.status = ANY(%s)", "r.data_agendada <= %s"]
    params: List[Any] = [list(STATUS_ABERTOS), limite_data]

    if materia_id is not None:
        where.append("i.materia_id = %s")
        params.append(materia_id)
    if categoria:
        where.append("i.categoria = %s")
        params.append(categoria)

    params.append(max(1, limite))
    query = f"""
        SELECT
            r.id AS revisao_id, r.item_id, r.etapa, r.intervalo_dias, r.data_agendada,
            r.data_realizada, r.status, r.desempenho, r.observacao,
            i.topico, i.categoria, i.conteudo, i.detalhes_resposta,
            i.parent_id, i.materia_id, m.nome AS materia_nome
        FROM revisoes r
        JOIN itens_estudo i ON i.id = r.item_id
        LEFT JOIN materias m ON m.id = i.materia_id
        WHERE {' AND '.join(where)}
        ORDER BY r.data_agendada ASC, r.id ASC
        LIMIT %s;
    """

    conn = db.get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            return [_serializar_revisao(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()


def plano_revisoes(dias: int = 30, materia_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Monta o plano de revisões agrupado por dia, com a distribuição da escada restante.
    """
    hoje = agora_local().date()
    limite = hoje + timedelta(days=max(0, dias))

    where = ["r.status = ANY(%s)", "r.data_agendada <= %s"]
    params: List[Any] = [list(STATUS_ABERTOS), datetime.combine(limite, dtime(23, 59))]
    if materia_id is not None:
        where.append("i.materia_id = %s")
        params.append(materia_id)

    query = f"""
        SELECT
            r.id AS revisao_id, r.item_id, r.etapa, r.intervalo_dias, r.data_agendada,
            r.status, i.topico, i.categoria, i.detalhes_resposta, i.parent_id,
            i.materia_id, m.nome AS materia_nome
        FROM revisoes r
        JOIN itens_estudo i ON i.id = r.item_id
        LEFT JOIN materias m ON m.id = i.materia_id
        WHERE {' AND '.join(where)}
        ORDER BY r.data_agendada ASC
    """

    conn = db.get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    por_dia: Dict[str, Dict[str, Any]] = {}
    atrasadas: List[Dict[str, Any]] = []
    for r in rows:
        agendada: datetime = r["data_agendada"]
        registro = {
            "revisao_id": r["revisao_id"],
            "item_id": r["item_id"],
            "topico": r["topico"],
            "categoria": r["categoria"],
            "materia_nome": r["materia_nome"],
            "etapa": r["etapa"],
            "intervalo_dias": r["intervalo_dias"],
            "status": r["status"],
        }
        if agendada.date() < hoje:
            registro["atrasada_dias"] = (hoje - agendada.date()).days
            atrasadas.append(registro)
            continue
        chave = agendada.date().isoformat()
        bucket = por_dia.setdefault(chave, {"data": chave, "total": 0, "itens": []})
        bucket["total"] += 1
        bucket["itens"].append(registro)

    dias_ordenados = [por_dia[k] for k in sorted(por_dia.keys())]
    return {
        "hoje": hoje.isoformat(),
        "horizonte_dias": dias,
        "total_agendado": len(rows),
        "total_atrasado": len(atrasadas),
        "atrasadas": sorted(atrasadas, key=lambda x: -x.get("atrasada_dias", 0)),
        "dias": dias_ordenados,
    }


def estatisticas() -> Dict[str, Any]:
    """Resumo do estado do ciclo de revisões para o dashboard e para o agente."""
    conn = db.get_db_connection()
    hoje = agora_local()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status, COUNT(*) AS qtd FROM revisoes GROUP BY status;")
            por_status = {r["status"]: r["qtd"] for r in cur.fetchall()}

            cur.execute("""
                SELECT 
                    COUNT(*) FILTER (WHERE status = ANY(%s) AND data_agendada::date <= %s) AS devidas,
                    COUNT(*) FILTER (WHERE status = ANY(%s) AND data_agendada::date < %s) AS atrasadas,
                    COUNT(*) FILTER (WHERE status = ANY(%s) AND data_agendada::date > %s AND data_agendada::date <= %s) AS proximos_7,
                    COUNT(DISTINCT item_id) AS itens_cobertos
                FROM revisoes;
            """, (
                list(STATUS_ABERTOS), hoje.date(),
                list(STATUS_ABERTOS), hoje.date(),
                list(STATUS_ABERTOS), hoje.date(), (hoje + timedelta(days=7)).date()
            ))
            res = cur.fetchone()
            devidas = res["devidas"] or 0
            atrasadas = res["atrasadas"] or 0
            proximos_7 = res["proximos_7"] or 0
            itens_cobertos = res["itens_cobertos"] or 0

            cur.execute(
                """
                SELECT i.categoria, COUNT(*) AS qtd
                FROM revisoes r JOIN itens_estudo i ON i.id = r.item_id
                WHERE r.status = ANY(%s) AND r.data_agendada::date <= %s
                GROUP BY i.categoria ORDER BY qtd DESC;
                """,
                (list(STATUS_ABERTOS), hoje.date()),
            )
            por_categoria = {r["categoria"]: r["qtd"] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(*) AS qtd FROM itens_estudo;")
            total_itens = cur.fetchone()["qtd"]

        return {
            "agora_local": hoje.isoformat(),
            "escada_dias": intervalos(),
            "por_status": por_status,
            "devidas_hoje": devidas,
            "atrasadas": atrasadas,
            "proximos_7_dias": proximos_7,
            "por_categoria_devidas": por_categoria,
            "itens_cobertos": itens_cobertos,
            "total_itens_base": total_itens,
            "cobertura_percentual": round(100.0 * itens_cobertos / total_itens, 1) if total_itens else 0.0,
        }
    finally:
        conn.close()


# =====================================================================
# REGISTRO DE RESPOSTA (coração do ciclo)
# =====================================================================

def proxima_etapa(etapa: int, desempenho: int, ladder: Sequence[int]) -> int:
    """
    Calcula a próxima etapa da escada segundo o desempenho autorreportado.
      0 -> reinicia na etapa 0
      1 -> repete a etapa atual
      2/3 -> avança uma etapa
    """
    if desempenho <= 0:
        return 0
    if desempenho == 1:
        return etapa
    return etapa + 1


def registrar_resposta(
    feita: bool,
    desempenho: Optional[int] = None,
    revisao_id: Optional[int] = None,
    item_id: Optional[int] = None,
    observacao: Optional[str] = None
) -> Dict[str, Any]:
    """
    Registra a resposta do usuário sobre uma revisão agendada e reprograma o ciclo.

    - feita=True  -> marca como 'feita', atualiza o item e agenda a próxima etapa.
    - feita=False -> marca como 'adiada' e reprograma para o dia seguinte (mantém a etapa).
    """
    if revisao_id is None and item_id is None:
        raise ValueError("Informe revisao_id ou item_id.")

    ladder = intervalos()
    conn = db.get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if revisao_id is not None:
                cur.execute("SELECT * FROM revisoes WHERE id = %s;", (revisao_id,))
            else:
                cur.execute(
                    """
                    SELECT * FROM revisoes
                    WHERE item_id = %s AND status = ANY(%s)
                    ORDER BY data_agendada ASC LIMIT 1;
                    """,
                    (item_id, list(STATUS_ABERTOS)),
                )
            revisao = cur.fetchone()
            if not revisao:
                raise ValueError(
                    f"Nenhuma revisão em aberto encontrada (revisao_id={revisao_id}, item_id={item_id})."
                )

            revisao = dict(revisao)
            agora = agora_local()
            etapa_atual = int(revisao["etapa"])
            item_ref = int(revisao["item_id"])

            # ---------- NÃO FEZ: adia para amanhã ----------
            if not feita:
                nova_data = _data_alvo(agora, 1)
                cur.execute(
                    """
                    UPDATE revisoes
                    SET status = %s, data_agendada = %s, observacao = %s
                    WHERE id = %s;
                    """,
                    (STATUS_ADIADA, nova_data, observacao, revisao["id"]),
                )
                conn.commit()
                logger.info(f"[revisoes] Revisão {revisao['id']} adiada para {nova_data.isoformat()}.")
                return {
                    "revisao_id": revisao["id"],
                    "item_id": item_ref,
                    "feita": False,
                    "status": STATUS_ADIADA,
                    "data_agendada": nova_data.isoformat(),
                    "etapa": etapa_atual,
                    "intervalo_dias": int(revisao["intervalo_dias"]),
                    "ciclo_concluido": False,
                    "mensagem": "Revisão adiada para amanhã (a etapa foi mantida).",
                }

            # ---------- FEZ: fecha e reprograma ----------
            desp = 2 if desempenho is None else max(0, min(3, int(desempenho)))
            cur.execute(
                """
                UPDATE revisoes
                SET status = %s, data_realizada = %s, desempenho = %s, observacao = %s
                WHERE id = %s;
                """,
                (STATUS_FEITA, agora, desp, observacao, revisao["id"]),
            )

            next_etapa = proxima_etapa(etapa_atual, desp, ladder)
            ciclo_concluido = next_etapa >= len(ladder)

            nova_revisao_id = None
            nova_data = None
            if ciclo_concluido:
                proximo_intervalo = None
            else:
                proximo_intervalo = ladder[next_etapa]
                nova_data = _data_alvo(agora, proximo_intervalo)
                cur.execute(
                    """
                    INSERT INTO revisoes (item_id, etapa, intervalo_dias, data_agendada, status, criado_em)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (item_ref, next_etapa, proximo_intervalo, nova_data, STATUS_PENDENTE, agora),
                )
                nova_revisao_id = cur.fetchone()["id"]

            # Atualiza os metadados de revisão do próprio item de estudo
            cur.execute(
                """
                UPDATE itens_estudo
                SET data_ultima_revisao = %s, qtd_revisoes = COALESCE(qtd_revisoes, 0) + 1
                WHERE id = %s;
                """,
                (agora, item_ref),
            )

            conn.commit()

        logger.info(
            f"[revisoes] Revisão {revisao['id']} concluída (desempenho={desp}). "
            f"Próxima: {nova_data.isoformat() if nova_data else 'ciclo encerrado'}"
        )
        return {
            "revisao_id": revisao["id"],
            "item_id": item_ref,
            "feita": True,
            "status": STATUS_FEITA,
            "desempenho": desp,
            "etapa_anterior": etapa_atual,
            "etapa": next_etapa,
            "intervalo_dias": proximo_intervalo,
            "ciclo_concluido": ciclo_concluido,
            "proxima_revisao_id": nova_revisao_id,
            "proxima_data": nova_data.isoformat() if nova_data else None,
            "etapas_totais": len(ladder),
            "mensagem": (
                "Ciclo de revisão concluído — conteúdo consolidado."
                if ciclo_concluido or nova_data is None
                else f"Próxima revisão agendada para {nova_data.date().isoformat()} (+{proximo_intervalo}d)."
            ),
        }
    finally:
        conn.close()


def registrar_respostas_lote(respostas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Registra várias respostas de uma vez (usado pelo agente em lote)."""
    resultados = []
    for r in respostas:
        try:
            resultados.append(
                registrar_resposta(
                    feita=bool(r.get("feita", True)),
                    desempenho=r.get("desempenho"),
                    revisao_id=r.get("revisao_id"),
                    item_id=r.get("item_id"),
                    observacao=r.get("observacao"),
                )
            )
        except Exception as e:
            resultados.append({"erro": str(e), "entrada": r})
    return resultados