"""Testes do detector de tipo de questão e avaliador interativo do Telegram Bot."""
import pytest
from src.bot.question_helpers import (
    normalize_ce_value,
    extract_multiple_choice_letter,
    detect_alternatives_in_text,
    detect_question_type,
    evaluate_answer,
)


def test_normalize_ce_value_direto():
    assert normalize_ce_value("CERTO") == "C"
    assert normalize_ce_value("ERRADO") == "E"
    assert normalize_ce_value("C") == "C"
    assert normalize_ce_value("E") == "E"
    assert normalize_ce_value("c") == "C"
    assert normalize_ce_value("e") == "E"


def test_normalize_ce_value_frases_comuns():
    assert normalize_ce_value("Certo. A questão descreve corretamente uma das três hipóteses...") == "C"
    assert normalize_ce_value("Errado. A Constituição assegura o direito de livre associação...") == "E"
    assert normalize_ce_value("Falso. Militares são agentes públicos.") == "E"
    assert normalize_ce_value("Verdadeiro. O servidor goza de estabilidade.") == "C"
    assert normalize_ce_value("Item certo. Explicação: Requisitos como altura...") == "C"
    assert normalize_ce_value("Gabarito: Certo") == "C"
    assert normalize_ce_value("Gabarito: ERRADO") == "E"


def test_normalize_ce_value_invalido():
    assert normalize_ce_value("") is None
    assert normalize_ce_value("Letra D") is None
    assert normalize_ce_value("Em lei formal e material.") is None


def test_extract_multiple_choice_letter():
    assert extract_multiple_choice_letter("D") == "D"
    assert extract_multiple_choice_letter("Letra D. A CF exige...") == "D"
    assert extract_multiple_choice_letter("A letra D.") == "D"
    assert extract_multiple_choice_letter("Alternativa B") == "B"
    assert extract_multiple_choice_letter("Opção C") == "C"
    assert extract_multiple_choice_letter("Gabarito: Letra A") == "A"
    assert extract_multiple_choice_letter("(E)") == "E"
    assert extract_multiple_choice_letter("CERTO") is None
    assert extract_multiple_choice_letter("") is None


def test_detect_alternatives_in_text():
    texto = (
        "Qual o prazo de validade do concurso?\n"
        "A) Até 2 anos, prorrogável uma vez por igual período.\n"
        "B) Até 1 ano, improrrogável.\n"
        "C) Até 3 anos.\n"
        "D) Até 4 anos.\n"
        "E) Indeterminado.\n"
    )
    alts = detect_alternatives_in_text(texto)
    assert alts == ["A", "B", "C", "D", "E"]


def test_detect_question_type_cebraspe():
    res = detect_question_type(
        conteudo="Os conselhos de fiscalização profissional possuem natureza de autarquia corporativa.",
        gabarito="ERRADO"
    )
    assert res["tipo"] == "ce"
    assert res["gabarito_norm"] == "E"
    assert res["gabarito_label"] == "Errado"
    assert len(res["opcoes"]) == 2
    assert res["opcoes"][0] == ("🟢 Certo", "C")
    assert res["opcoes"][1] == ("🔴 Errado", "E")


def test_detect_question_type_multipla_escolha():
    conteudo = (
        "Sobre agentes públicos, assinale a opção correta:\n"
        "A) São sempre remunerados.\n"
        "B) Não respondem civilmente.\n"
        "C) O concurso é prescindível para cargo efetivo.\n"
        "D) Cargos em comissão prescindem de concurso público.\n"
    )
    gabarito = "Letra D. A CF exige que os cargos em comissão..."
    res = detect_question_type(conteudo, gabarito)
    assert res["tipo"] == "multipla"
    assert res["gabarito_norm"] == "D"
    assert res["gabarito_label"] == "Alternativa D"
    assert len(res["opcoes"]) >= 4


def test_detect_question_type_aberta_fallback():
    conteudo = "Explique a diferença conceitual entre desconcentração e descentralização."
    gabarito = "Desconcentração é a distribuição interna de competências; descentralização é a transferência externa."
    res = detect_question_type(conteudo, gabarito)
    assert res["tipo"] == "aberta"
    assert res["opcoes"] == []


def test_evaluate_answer_cebraspe_acerto_e_erro():
    # Usuário marca Certo em questão Certa
    acertou, opt_lbl, gab_lbl = evaluate_answer("C", "CERTO", "ce")
    assert acertou is True
    assert opt_lbl == "Certo"
    assert gab_lbl == "Certo"

    # Usuário marca Errado em questão Certa
    acertou, opt_lbl, gab_lbl = evaluate_answer("E", "CERTO", "ce")
    assert acertou is False
    assert opt_lbl == "Errado"
    assert gab_lbl == "Certo"

    # Usuário marca Errado em questão Falsa com explicação longa
    acertou, opt_lbl, gab_lbl = evaluate_answer("E", "Falso. Militares são agentes públicos.", "ce")
    assert acertou is True
    assert opt_lbl == "Errado"
    assert gab_lbl == "Errado"


def test_evaluate_answer_multipla_escolha():
    # Usuário escolhe D em questão de Letra D
    acertou, opt_lbl, gab_lbl = evaluate_answer("D", "Letra D. Fundamentação...", "multipla")
    assert acertou is True
    assert opt_lbl == "Alternativa D"
    assert gab_lbl == "Alternativa D"

    # Usuário escolhe A em questão de Letra D
    acertou, opt_lbl, gab_lbl = evaluate_answer("A", "Letra D. Fundamentação...", "multipla")
    assert acertou is False
    assert opt_lbl == "Alternativa A"
    assert gab_lbl == "Alternativa D"
