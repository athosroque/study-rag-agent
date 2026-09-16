import pytest
from src.models import (
    ItemEstudo,
    ConjuntoItemsExtraidos,
    DecisaoIntegracao,
    RelatorioReconciliacao,
    FragmentoConhecimento
)


def test_item_estudo_creation():
    item = ItemEstudo(
        topico="Direito Administrativo - Atributos",
        categoria="teoria",
        conteudo="A autoexecutoriedade permite que a Administração execute diretamente seus atos.",
        detalhes_resposta="Não necessita de autorização judicial prévia."
    )
    assert item.topico == "Direito Administrativo - Atributos"
    assert item.categoria == "teoria"
    assert "autoexecutoriedade" in item.conteudo


def test_decisao_integracao_adicionar_fragmento():
    item = ItemEstudo(
        topico="Direito Administrativo - Atributos",
        categoria="pegadinha",
        conteudo="Atenção: A cobrança de multa NÃO goza de autoexecutoriedade.",
        detalhes_resposta="Necessita de execução fiscal em juízo."
    )
    decisao = DecisaoIntegracao(
        item=item,
        acao="ADICIONAR_FRAGMENTO",
        item_id_referencia=42,
        conteudo_incremental="Cobrança de multa exige execução fiscal e não é autoexecutória.",
        justificativa="Enriquece o conceito de autoexecutoriedade com a principal pegadinha de prova."
    )
    assert decisao.acao == "ADICIONAR_FRAGMENTO"
    assert decisao.item_id_referencia == 42
    assert decisao.conteudo_incremental is not None


def test_decisao_integracao_descartar():
    item = ItemEstudo(
        topico="Direito Administrativo",
        categoria="teoria",
        conteudo="O ato administrativo é uma manifestação de vontade.",
        detalhes_resposta=None
    )
    decisao = DecisaoIntegracao(
        item=item,
        acao="DESCARTAR",
        item_id_referencia=None,
        conteudo_incremental=None,
        justificativa="Definição genérica já redundante na base de dados."
    )
    assert decisao.acao == "DESCARTAR"


def test_relatorio_reconciliacao():
    item = ItemEstudo(topico="T", categoria="sacada", conteudo="Mnemônico PATI")
    dec = DecisaoIntegracao(item=item, acao="CRIAR_NOVO", justificativa="Inédito")
    relatorio = RelatorioReconciliacao(decisoes=[dec])
    assert len(relatorio.decisoes) == 1
    assert relatorio.decisoes[0].acao == "CRIAR_NOVO"


def test_questao_cespe_creation():
    from src.models import QuestaoCespe, ConjuntoQuestoesCespe
    q = QuestaoCespe(
        topico="Atributos do Ato",
        enunciado="A autoexecutoriedade é atributo presente em todos os atos administrativos, sem exceção.",
        gabarito="ERRADO",
        justificativa="A autoexecutoriedade não existe em todos os atos (ex: cobrança de multa).",
        pegadinha_explicada="A banca usou o termo absolutista 'todos... sem exceção'."
    )
    assert q.gabarito == "ERRADO"
    conjunto = ConjuntoQuestoesCespe(questoes=[q])
    assert len(conjunto.questoes) == 1
    assert conjunto.questoes[0].topico == "Atributos do Ato"
