import pytest
from src import db


def test_normalize_materia_canonical_synonyms():
    """Valida que variações de escrita e sinônimos convergem para o mesmo nome canônico e slug."""
    # Língua Portuguesa
    cases_pt = [
        "portugues",
        "Português",
        "PORTUGUÊS",
        "  lingua portuguesa  ",
        "Língua Portuguesa",
        "gramatica",
        "redacao"
    ]
    for case in cases_pt:
        nome, slug = db.normalize_materia(case)
        assert nome == "Língua Portuguesa"
        assert slug == "lingua_portuguesa"

    # Direito Constitucional
    cases_const = [
        "direito constitucional",
        "Dir Constitucional",
        "dir. constitucional",
        "constitucional",
        "constituicao"
    ]
    for case in cases_const:
        nome, slug = db.normalize_materia(case)
        assert nome == "Direito Constitucional"
        assert slug == "direito_constitucional"

    # RLM
    cases_rlm = [
        "rlm",
        "raciocinio logico",
        "Raciocínio Lógico-Matemático",
        "matematica"
    ]
    for case in cases_rlm:
        nome, slug = db.normalize_materia(case)
        assert nome == "Raciocínio Lógico e Matemática"
        assert slug == "raciocinio_logico_matematico"

    # Informática / TI
    for case in ["informatica", "Informática", "ti", "tecnologia da informacao"]:
        nome, slug = db.normalize_materia(case)
        assert nome == "Informática"
        assert slug == "informatica"


def test_normalize_materia_fallback_generic():
    """Valida a normalização genérica com formatação de título para disciplinas não mapeadas."""
    nome, slug = db.normalize_materia("direito tributario e financeiro")
    assert nome == "Direito Tributario e Financeiro"
    assert slug == "direito_tributario_e_financeiro"

    # Nulo ou vazio retorna Geral
    assert db.normalize_materia(None) == ("Geral", "geral")
    assert db.normalize_materia("") == ("Geral", "geral")
    assert db.normalize_materia("   ") == ("Geral", "geral")


def test_get_or_create_materia_deduplication():
    """Valida que get_or_create_materia não cria registros redundantes no banco."""
    # Chamadas com variações de escrita devem retornar exatamente o mesmo id
    id1 = db.get_or_create_materia("Português")
    id2 = db.get_or_create_materia("portugues")
    id3 = db.get_or_create_materia("  LÍNGUA PORTUGUESA  ")
    id4 = db.get_or_create_materia("gramatica")

    assert id1 == id2 == id3 == id4

    # Disciplina diferente deve gerar outro id
    id_const = db.get_or_create_materia("Direito Constitucional")
    id_const2 = db.get_or_create_materia("dir constitucional")
    assert id_const == id_const2
    assert id_const != id1


def test_list_materias_structure():
    """Valida que list_materias retorna a lista com as propriedades agregadas."""
    materias = db.list_materias()
    assert len(materias) >= 1

    pt = next((m for m in materias if m["slug"] == "lingua_portuguesa"), None)
    assert pt is not None
    assert pt["nome"] == "Língua Portuguesa"
    assert "qtd_topicos" in pt
    assert "qtd_itens" in pt
    assert pt["qtd_itens"] >= 40
