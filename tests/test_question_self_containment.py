"""Testes unitários e de integração para validação de autossuficiência de questões."""
import pytest
from src.revisoes import verificar_autossuficiencia_enunciado, validar_item_para_revisao
import src.db as db

pytestmark_db = pytest.mark.skipif(
    not hasattr(db, "get_db_connection"),
    reason="Requer conexão com o banco de dados"
)


def test_autossuficiencia_questao_truncada_rejeitada():
    """Questão que cita 'do texto' mas só tem fragmento de 1 ou 2 palavras entre aspas deve ser rejeitada."""
    enunciado_antigo = (
        "Pergunta: Estaria mantida a correção gramatical do texto caso o pronome 'os' em 'os cerca' "
        "fosse empregado em posição enclítica ('cerca-os')?"
    )
    valido, motivo = verificar_autossuficiencia_enunciado(enunciado_antigo)
    assert valido is False
    assert "Enunciado truncado ou não autossuficiente" in motivo
    assert "referência a 'texto'" in motivo


def test_autossuficiencia_linhas_sem_trecho_rejeitada():
    """Menção a linha de texto sem excerto de apoio deve ser rejeitada."""
    enunciado = "Na linha 14 do texto, o vocábulo 'portanto' introduz uma oração subordinada causal."
    valido, motivo = verificar_autossuficiencia_enunciado(enunciado)
    assert valido is False
    assert "Enunciado truncado ou não autossuficiente" in motivo


def test_autossuficiencia_questao_com_trecho_longo_valida():
    """Questão que cita o texto mas transcreve a oração/trecho completo entre aspas deve ser aceita."""
    enunciado_com_trecho = (
        "Em relação às relações de sentido do texto, julgue o item a seguir: "
        "O sentido original do texto seria mantido caso o trecho "
        "'Quase todas as religiões desapareceriam com as escrituras em que foram expressas' "
        "fosse reescrito como 'Todas as crenças religiosas desvaneceriam junto às suas escrituras'."
    )
    valido, motivo = verificar_autossuficiencia_enunciado(enunciado_com_trecho)
    assert valido is True
    assert motivo is None


def test_autossuficiencia_questao_conceitual_valida():
    """Questões conceituais puras sem referência a texto externo são válidas."""
    enunciado_conceitual = (
        "Os cargos em comissão destinam-se exclusivamente às atribuições de direção, "
        "chefia e assessoramento no âmbito da administração pública."
    )
    valido, motivo = verificar_autossuficiencia_enunciado(enunciado_conceitual)
    assert valido is True
    assert motivo is None


def test_autossuficiencia_questao_contextualizada_hipotetica():
    """Questão que contextualiza a hipótese ('Considere uma oração...') é válida."""
    enunciado_corrigido = (
        "Considere uma oração hipotética em que haja uma palavra atrativa invariável "
        "(como advérbio ou pronome indefinido) antecedendo a estrutura verbal: '... [termo invariável] os cerca ...'. "
        "Nesse contexto, estaria mantida a correção gramatical caso o pronome fosse empregado em posição enclítica ('cerca-os')?"
    )
    valido, motivo = verificar_autossuficiencia_enunciado(enunciado_corrigido)
    assert valido is True
    assert motivo is None


def test_autossuficiencia_enunciado_vazio():
    assert verificar_autossuficiencia_enunciado("")[0] is False
    assert verificar_autossuficiencia_enunciado(None)[0] is False


@pytestmark_db
def test_validar_item_para_revisao_item_39_atualizado():
    """O item 39 agora deve ser validado com sucesso após o saneamento."""
    valido, motivo = validar_item_para_revisao(39)
    assert valido is True
    assert "válido para revisão" in motivo
