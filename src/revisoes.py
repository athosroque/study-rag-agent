"""
Motor de Revisão Espaçada baseado na Curva do Esquecimento de Hermann Ebbinghaus.

Regras do ciclo:
  - Cada item de estudo (teoria/sacada/pegadinha/questao) entra na escada de intervalos.
  - Escada padrão (dias): 1, 7, 15, 30 (configurável via REVISAO_INTERVALOS_DIAS).
  - Autoavaliação do desempenho (0 a 3):
      0 = não lembrei / errei  -> volta para a etapa 0 (recomeça o ciclo)
      1 = lembrei com dificuldade -> repete a mesma etapa (intervalo não avança)
      2 = acertei              -> avança uma etapa
      3 = fácil / domino       -> avança uma etapa
  - Se o usuário não fez a revisão, ela é adiada para o dia seguinte (mantendo a etapa).
  - Ao esgotar a escada, o ciclo é considerado concluído.
"""
import logging
import re
from datetime import datetime, timedelta, time as dtime
from typing import List, Dict, Any, Optional, Sequence

from psycopg2.extras import RealDictCursor

from src.config import settings
from src import db

logger = logging.getLogger(__name__)

DEFAULT_INTERVALOS: List[int] = [1, 7, 15, 30]

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

def verificar_autossuficiencia_enunciado(conteudo: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Verifica se o enunciado de uma questão é autossuficiente para resolução isolada no bot/app.
    Identifica se a questão faz referências cegas a textos, linhas ou parágrafos externos
    sem fornecer a oração, frase ou trecho delimitado correspondente.
    Retorna (valido: bool, motivo: Optional[str]).
    """
    if not conteudo:
        return False, "Enunciado vazio."

    texto = conteudo.strip()
    texto_lower = texto.lower()

    # Padrões que indicam dependência textual externa
    padroes_referencia_externa = [
        (r'\b(?:no|do|deste|desse|ao)\s+texto\b', "referência a 'texto'"),
        (r'\b(?:nas?|às?)\s+linhas?\s+\d+\b', "referência a número de linha"),
        (r'\b(?:no|do)\s+(?:primeiro|segundo|terceiro|último|ultimo)\s+parágrafo\b', "referência a parágrafo externo"),
        (r'\b(?:no|do)\s+último\s+período\b', "referência a 'último período'"),
        (r'\bsegundo\s+o\s+autor\b', "referência a 'segundo o autor'"),
    ]

    tem_referencia = False
    motivo_detectado = ""
    for padrao, desc in padroes_referencia_externa:
        if re.search(padrao, texto_lower):
            tem_referencia = True
            motivo_detectado = desc
            break

    if not tem_referencia:
        return True, None

    # Se há referência textual, verificamos se há um trecho/oração suporte substantivo fornecido
    # Extrai segmentos delimitados por aspas pareadas sem pontes indevidas
    segs: List[str] = []
    parts_double = texto.split('"')
    if len(parts_double) >= 3:
        for i in range(1, len(parts_double), 2):
            segs.append(parts_double[i])
    parts_single = texto.split("'")
    if len(parts_single) >= 3:
        for i in range(1, len(parts_single), 2):
            segs.append(parts_single[i])
    segs.extend(re.findall(r'“([^”]+)”', texto))
    segs.extend(re.findall(r'«([^»]+)»', texto))

    for s in segs:
        s_clean = s.strip()
        # Trecho substantivo com extensão mínima e contendo pelo menos 3 palavras
        if len(s_clean) >= 15 and len(s_clean.split()) >= 3:
            return True, None

    # Também checa se o enunciado introduz explicitamente um trecho/oração suporte com instrução e comprimento
    # Ex: 'Considere a seguinte frase: ...' ou 'Julgue o item a partir da oração: ...'
    if re.search(r'(?:considere|analise|observe|leia)\s+(?:a\s+frase|a\s+oração|o\s+trecho|o\s+seguinte)', texto_lower):
        if len(texto) >= 60:
            return True, None

    return False, f"Enunciado truncado ou não autossuficiente ({motivo_detectado} sem trecho/oração suporte no corpo da questão)."


def validar_item_para_revisao(item_id: int) -> tuple[bool, str]:
    """
    Valida se um item de estudo pode ser agendado/enviado para o ciclo de revisão ativa (Ebbinghaus).
    Regras estritas:
      - Deve existir no banco
      - categoria DEVE ser 'questao'
      - conteudo não pode estar vazio (enunciado substancial)
      - enunciado DEVE ser autossuficiente (sem dependência cega de textos externos não fornecidos)
      - detalhes_resposta/gabarito não pode estar vazio (gabarito substancial)
    """
    conn = db.get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, categoria, conteudo, gabarito, detalhes_resposta
                FROM itens_estudo
                WHERE id = %s;
            """, (item_id,))
            row = cur.fetchone()
            if not row:
                return False, f"Item {item_id} não encontrado."

            if row["categoria"] != "questao":
                return False, f"Item {item_id} é da categoria '{row['categoria']}'. Revisões ativas são exclusivas para 'questao'."

            conteudo = (row.get("conteudo") or "").strip()
            if len(conteudo) < 5:
                return False, f"Item {item_id} não possui enunciado/pergunta válido."

            autossuficiente, motivo_auto = verificar_autossuficiencia_enunciado(conteudo)
            if not autossuficiente:
                logger.warning(f"[revisoes] Item {item_id} rejeitado na validação: {motivo_auto}")
                return False, f"Item {item_id}: {motivo_auto}"

            resposta = (row.get("gabarito") or row.get("detalhes_resposta") or "").strip()
            if len(resposta) < 2:
                return False, f"Item {item_id} não possui gabarito/resposta válido."

            return True, "Item válido para revisão."
    finally:
        conn.close()


def agendar_primeira_revisao(
    item_id: int,
    base_date: Optional[datetime] = None,
    substituir_concluidas: bool = False,
    validar: bool = True
) -> Optional[int]:
    """
    Cria a primeira revisão (etapa 0) de um item, se ele ainda não tiver ciclo aberto.
    Valida se o item é elegível (categoria 'questao' com pergunta e gabarito).
    Retorna o ID da revisão criada ou None se já existia ou foi rejeitado.
    """
    if validar:
        valido, motivo = validar_item_para_revisao(item_id)
        if not valido:
            logger.warning(f"[revisoes] Agendamento recusado: {motivo}")
            return None

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
    categorias: Optional[Sequence[str]] = ("questao",),
    base_date: Optional[datetime] = None
) -> Dict[str, int]:
    """
    Cria a primeira revisão para todo item de estudo que ainda não possui nenhuma revisão.
    Por padrão, agenda APENAS questões ('questao'), mantendo teorias sob consulta sob demanda.
    Usa como base a data da última revisão do item (ou a data de criação).
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
    categoria: Optional[str] = "questao",
    limite: int = 50
) -> List[Dict[str, Any]]:
    """
    Lista revisões em aberto com vencimento até hoje + `dias`.
    Por padrão, restringe a categoria='questao' (revisão ativa apenas por questão).
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
            i.topico, i.categoria, i.conteudo, i.gabarito, i.detalhes_resposta,
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
            r.status, i.topico, i.categoria, i.gabarito, i.detalhes_resposta, i.parent_id,
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


def estatisticas(categoria: Optional[str] = "questao") -> Dict[str, Any]:
    """Resumo do estado do ciclo de revisões para o dashboard e para o agente (padrão: questao)."""
    conn = db.get_db_connection()
    hoje = agora_local()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status, COUNT(*) AS qtd FROM revisoes GROUP BY status;")
            por_status = {r["status"]: r["qtd"] for r in cur.fetchall()}

            cat_filter = "AND i.categoria = %s" if categoria else ""
            cat_param = [categoria] if categoria else []

            query_devidas = f"""
                SELECT 
                    COUNT(*) FILTER (WHERE r.status = ANY(%s) AND r.data_agendada::date <= %s) AS devidas,
                    COUNT(*) FILTER (WHERE r.status = ANY(%s) AND r.data_agendada::date < %s) AS atrasadas,
                    COUNT(*) FILTER (WHERE r.status = ANY(%s) AND r.data_agendada::date > %s AND r.data_agendada::date <= %s) AS proximos_7,
                    COUNT(DISTINCT r.item_id) AS itens_cobertos
                FROM revisoes r
                JOIN itens_estudo i ON i.id = r.item_id
                WHERE 1=1 {cat_filter};
            """
            params_devidas = [
                list(STATUS_ABERTOS), hoje.date(),
                list(STATUS_ABERTOS), hoje.date(),
                list(STATUS_ABERTOS), hoje.date(), (hoje + timedelta(days=7)).date()
            ] + cat_param

            cur.execute(query_devidas, tuple(params_devidas))
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

            if categoria:
                cur.execute("SELECT COUNT(*) AS qtd FROM itens_estudo WHERE categoria = %s;", (categoria,))
            else:
                cur.execute("SELECT COUNT(*) AS qtd FROM itens_estudo;")
            total_itens = cur.fetchone()["qtd"]

        return {
            "agora_local": hoje.isoformat(),
            "escada_dias": intervalos(),
            "categoria_filtro": categoria,
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