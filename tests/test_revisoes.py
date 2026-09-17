"""Testes do motor de revisão espaçada (Curva do Esquecimento - Ebbinghaus)."""
from datetime import datetime, timedelta

import pytest

from src import revisoes
from src.models import RespostaRevisaoRequest, RespostaRevisaoLoteRequest


# ---------------------------------------------------------------------
# Escada de intervalos
# ---------------------------------------------------------------------

def test_intervalos_escada_padrao(monkeypatch):
    monkeypatch.setattr(revisoes.settings, "REVISAO_INTERVALOS_DIAS", "")
    ladder = revisoes.intervalos()
    assert ladder == [1, 7, 15, 30]
    assert ladder == sorted(ladder)


def test_intervalos_sao_crescentes_e_positivos():
    ladder = revisoes.intervalos()
    assert all(i > 0 for i in ladder)
    assert len(ladder) == len(set(ladder))


# ---------------------------------------------------------------------
# Cálculo da próxima etapa (regra de ouro do ciclo)
# ---------------------------------------------------------------------

def test_proxima_etapa_avanca_quando_acerta():
    assert revisoes.proxima_etapa(0, 2, [1, 2, 4]) == 1
    assert revisoes.proxima_etapa(1, 3, [1, 2, 4]) == 2


def test_proxima_etapa_reinicia_quando_erra():
    assert revisoes.proxima_etapa(3, 0, [1, 2, 4, 7]) == 0


def test_proxima_etapa_repete_quando_dificuldade():
    assert revisoes.proxima_etapa(2, 1, [1, 2, 4, 7]) == 2


def test_proxima_etapa_ultrapassa_escada_indica_ciclo_concluido():
    ladder = [1, 2, 4]
    assert revisoes.proxima_etapa(len(ladder) - 1, 2, ladder) >= len(ladder)


# ---------------------------------------------------------------------
# Cálculo de datas
# ---------------------------------------------------------------------

def test_data_alvo_respeita_intervalo_e_hora():
    base = datetime(2026, 9, 13, 23, 30)
    alvo = revisoes._data_alvo(base, 7)
    assert alvo.date() == datetime(2026, 9, 20).date()
    assert alvo.hour == revisoes.settings.REVISAO_HORA_AGENDAMENTO
    assert alvo.minute == 0


def test_agora_local_aplica_offset_do_fuso():
    utc = datetime.utcnow()
    local = revisoes.agora_local()
    delta_horas = (local - utc).total_seconds() / 3600
    assert abs(delta_horas - revisoes.settings.REVISAO_TZ_OFFSET_HORAS) < 0.01


# ---------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------

def test_resposta_revisao_request_defaults():
    req = RespostaRevisaoRequest(item_id=10)
    assert req.feita is True
    assert req.desempenho is None
    assert req.revisao_id is None


def test_resposta_revisao_request_rejeita_desempenho_invalido():
    with pytest.raises(Exception):
        RespostaRevisaoRequest(item_id=1, desempenho=9)
    with pytest.raises(Exception):
        RespostaRevisaoRequest(item_id=1, desempenho=-1)


def test_resposta_revisao_lote_request():
    lote = RespostaRevisaoLoteRequest(respostas=[
        {"item_id": 1, "feita": True, "desempenho": 2},
        {"item_id": 2, "feita": False},
    ])
    assert len(lote.respostas) == 2
    assert lote.respostas[1].feita is False


def test_registrar_resposta_exige_identificador():
    with pytest.raises(ValueError):
        revisoes.registrar_resposta(feita=True)


# ---------------------------------------------------------------------
# Integração com o banco (só roda se o PostgreSQL estiver acessível)
# ---------------------------------------------------------------------

def _db_disponivel() -> bool:
    try:
        conn = revisoes.db.get_db_connection()
        conn.close()
        return True
    except Exception:
        return False


pytestmark_db = pytest.mark.skipif(not _db_disponivel(), reason="PostgreSQL indisponível")


@pytestmark_db
def test_ciclo_completo_no_banco():
    """Cria item -> agenda -> responde -> confere avanço da etapa."""
    conn = revisoes.db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO itens_estudo (topico, categoria, conteudo, detalhes_resposta, fragmentos, criado_em, data_ultima_revisao, qtd_revisoes)
                VALUES ('TESTE_REVISAO_UNIT', 'questao', 'Pergunta de teste válida?', 'Gabarito oficial de teste', '[]'::jsonb, NOW(), NOW(), 0)
                RETURNING id;
            """)
            item_id = cur.fetchone()[0]
            conn.commit()
    finally:
        conn.close()

    try:
        rid = revisoes.agendar_primeira_revisao(item_id)
        assert rid is not None
        # Não deve duplicar
        assert revisoes.agendar_primeira_revisao(item_id) is None

        resultado = revisoes.registrar_resposta(feita=True, desempenho=2, revisao_id=rid)
        assert resultado["feita"] is True
        assert resultado["etapa"] == 1
        assert resultado["intervalo_dias"] == revisoes.intervalos()[1]
        assert resultado["proxima_revisao_id"] is not None

        # Responder de novo pelo item_id (revisão aberta seguinte)
        segunda = revisoes.registrar_resposta(feita=False, item_id=item_id)
        assert segunda["status"] == "adiada"
        assert segunda["etapa"] == 1
    finally:
        conn = revisoes.db.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM itens_estudo WHERE id = %s;", (item_id,))
                conn.commit()
        finally:
            conn.close()


@pytestmark_db
def test_validacao_rejeita_teoria_e_sem_gabarito():
    """Valida que itens de teoria ou sem resposta não podem ser agendados para revisão."""
    conn = revisoes.db.get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Item do tipo teoria
            cur.execute("""
                INSERT INTO itens_estudo (topico, categoria, conteudo, detalhes_resposta, criado_em, data_ultima_revisao)
                VALUES ('TESTE_TEORIA', 'teoria', 'Explicação conceitual teórica...', 'Nota teórica', NOW(), NOW())
                RETURNING id;
            """)
            teoria_id = cur.fetchone()[0]

            # 2. Questão sem gabarito
            cur.execute("""
                INSERT INTO itens_estudo (topico, categoria, conteudo, detalhes_resposta, criado_em, data_ultima_revisao)
                VALUES ('TESTE_QUESTAO_SEM_RESPOSTA', 'questao', 'Pergunta sem resposta?', NULL, NOW(), NOW())
                RETURNING id;
            """)
            invalida_id = cur.fetchone()[0]
            conn.commit()
    finally:
        conn.close()

    try:
        # Teoria deve ser recusada
        valido_t, motivo_t = revisoes.validar_item_para_revisao(teoria_id)
        assert valido_t is False
        assert "exclusivas para 'questao'" in motivo_t
        assert revisoes.agendar_primeira_revisao(teoria_id) is None

        # Questão sem gabarito deve ser recusada
        valido_q, motivo_q = revisoes.validar_item_para_revisao(invalida_id)
        assert valido_q is False
        assert "não possui gabarito" in motivo_q
        assert revisoes.agendar_primeira_revisao(invalida_id) is None
    finally:
        conn = revisoes.db.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM itens_estudo WHERE id IN (%s, %s);", (teoria_id, invalida_id))
                conn.commit()
        finally:
            conn.close()


@pytestmark_db
def test_get_related_study_items():
    """Valida busca de teoria/sacada/pegadinha relacionadas a um item."""
    rel = revisoes.db.get_related_study_items(7)
    if rel:
        assert rel["item_id"] == 7
        assert "teoria" in rel
        assert "sacada" in rel
        assert "pegadinha" in rel


@pytestmark_db
def test_backfill_cria_revisoes_para_base():
    resultado = revisoes.backfill_revisoes(categorias=["questao"])
    assert "revisoes_criadas" in resultado
    stats = revisoes.estatisticas(categoria="questao")
    assert stats["itens_cobertos"] >= 1
    assert isinstance(stats["escada_dias"], list)