import re
from typing import Optional, List, Tuple, Dict, Any


def normalize_ce_value(text: Optional[str]) -> Optional[str]:
    """
    Identifica se um texto ou gabarito corresponde a CERTO ('C') ou ERRADO ('E').
    Suporta Certo/Errado, Verdadeiro/Falso e Sim/Não.
    Retorna 'C', 'E' ou None.
    """
    if not text:
        return None
    
    clean = text.strip().lower()
    
    # Se começar explicitamente com Gabarito: ou Resposta:
    m_prefix = re.match(r'^(?:gabarito|resposta|gabarito\s+oficial)\s*[:=-]\s*(.*)', clean, re.IGNORECASE)
    if m_prefix:
        clean = m_prefix.group(1).strip()
    
    # 1. Checagem direta de assertiva de Certo / Sim
    if re.match(r'^(?:certo|correto|verdadeiro|item\s+certo|afirmativa\s+correta|assertiva\s+correta|sim)(?:\b|[.\s:;,-])', clean):
        return "C"
    if clean in ("c", "v", "s"):
        return "C"
        
    # 2. Checagem direta de assertiva de Errado / Não
    if re.match(r'^(?:errado|incorreto|falso|item\s+errado|afirmativa\s+falsa|assertiva\s+falsa|não|nao)(?:\b|[.\s:;,-])', clean):
        return "E"
    if clean in ("e", "f", "n"):
        return "E"

    # 3. Busca de padrão em frases compostas (ex: "Gabarito: Certo", "Resposta: Sim", "Item errado.")
    if re.search(r'\b(?:gabarito\s*(?:oficial)?\s*[:=-]?\s*(?:certo|correto|verdadeiro|sim)|o\s+item\s+está\s+certo|resposta\s*(?:final)?\s*[:=-]?\s*sim)\b', clean):
        return "C"
    if re.search(r'\b(?:gabarito\s*(?:oficial)?\s*[:=-]?\s*(?:errado|incorreto|falso|não|nao)|o\s+item\s+está\s+errado|resposta\s*(?:final)?\s*[:=-]?\s*não)\b', clean):
        return "E"

    return None


def extract_multiple_choice_letter(text: Optional[str]) -> Optional[str]:
    """
    Extrai uma letra de alternativa (A, B, C, D ou E) de um gabarito.
    Ex: 'Letra D', 'Alternativa B', 'A letra D.', 'Opção C', 'B'
    Retorna a letra em maiúsculo ('A'-'E') ou None.
    """
    if not text:
        return None
    
    clean = text.strip()
    
    # 1. Letra isolada
    if clean.upper() in ("A", "B", "C", "D", "E"):
        return clean.upper()

    # 2. Padrões comuns de indicação de gabarito
    patterns = [
        r'(?:letra|alternativa|opção)\s*[:=-]?\s*([A-E])\b',
        r'^(?:a\s+)?letra\s+([A-E])\b',
        r'gabarito\s*[:=-]?\s*(?:letra\s+|alternativa\s+)?([A-E])\b',
        r'^\(([A-E])\)',
        r'^([A-E])\)',
    ]
    for pat in patterns:
        m = re.search(pat, clean, re.IGNORECASE)
        if m:
            return m.group(1).upper()
            
    return None


def detect_alternatives_in_text(conteudo: str) -> List[str]:
    """
    Verifica se o enunciado contém alternativas de múltipla escolha como:
    A) ... B) ... C) ... D) ... (ou [A] [B] ou (A) (B))
    Retorna a lista de opções detectadas, ex: ['A', 'B', 'C', 'D', 'E'].
    """
    if not conteudo:
        return []

    # Procura marcadores de alternativas no texto (ex: A), (A), [A], a))
    matches = re.findall(r'(?:^|\n|\s)(?:\(?([A-E])\)|\[([A-E])\]|([A-E])\))\s+', conteudo)
    found = set()
    for t in matches:
        for letter in t:
            if letter:
                found.add(letter.upper())

    # Se encontrou ao menos A, B e C, consideramos múltipla escolha
    if {"A", "B", "C"}.issubset(found):
        options = ["A", "B", "C", "D"]
        if "E" in found:
            options.append("E")
        return options

    return []


def detect_question_type(conteudo: str, gabarito: Optional[str] = None) -> Dict[str, Any]:
    """
    Detecta a categoria interativa da questão:
      - 'ce': Certo / Errado (padrão CESPE/Cebraspe)
      - 'multipla': Múltipla Escolha (A, B, C, D, E)
      - 'aberta': Questão livre / conceitual (fallback)
    """
    raw_gab = (gabarito or "").strip()
    raw_cont = (conteudo or "").strip()

    # 1. Testa se o gabarito ou enunciado é claramente Certo/Errado
    ce_norm = normalize_ce_value(raw_gab)
    
    # Se o gabarito não deu certeza, verifica se o enunciado é explicitamente Cebraspe
    ce_markers = ["julgue o item", "julgue os itens", "julgue a assertiva", "certo ou errado", "(c/e)"]
    has_ce_marker = any(marker in raw_cont.lower() for marker in ce_markers)

    if ce_norm:
        label = "Certo" if ce_norm == "C" else "Errado"
        return {
            "tipo": "ce",
            "gabarito_norm": ce_norm,
            "gabarito_label": label,
            "opcoes": [("🟢 Certo", "C"), ("🔴 Errado", "E")],
        }

    if has_ce_marker:
        # Tenta normalizar novamente com heurística mais relaxada
        return {
            "tipo": "ce",
            "gabarito_norm": None,
            "gabarito_label": raw_gab or "Gabarito não especificado",
            "opcoes": [("🟢 Certo", "C"), ("🔴 Errado", "E")],
        }

    # 2. Testa Múltipla Escolha
    mc_letter = extract_multiple_choice_letter(raw_gab)
    detected_alts = detect_alternatives_in_text(raw_cont)

    if mc_letter or detected_alts:
        options = detected_alts or ["A", "B", "C", "D", "E"]
        if mc_letter and mc_letter not in options:
            options.append(mc_letter)
            options.sort()

        label = f"Alternativa {mc_letter}" if mc_letter else (raw_gab or "Múltipla Escolha")
        return {
            "tipo": "multipla",
            "gabarito_norm": mc_letter,
            "gabarito_label": label,
            "opcoes": [(opt, opt) for opt in options],
        }

    # 3. Fallback: Aberta / Conceitual
    return {
        "tipo": "aberta",
        "gabarito_norm": None,
        "gabarito_label": raw_gab or "Gabarito disponível após revelação",
        "opcoes": [],
    }


def evaluate_answer(
    escolha: str,
    gabarito_raw: Optional[str],
    tipo: str = "ce"
) -> Tuple[bool, str, str]:
    """
    Avalia a resposta escolhida pelo usuário contra o gabarito oficial.
    Retorna:
      (acertou: bool, escolha_label: str, gabarito_label: str)
    """
    raw_gab = (gabarito_raw or "").strip()
    clean_escolha = (escolha or "").strip().upper()

    if tipo == "ce":
        escolha_label = "Certo" if clean_escolha == "C" else "Errado"
        gab_norm = normalize_ce_value(raw_gab)
        if gab_norm:
            gabarito_label = "Certo" if gab_norm == "C" else "Errado"
            acertou = (clean_escolha == gab_norm)
        else:
            gabarito_label = raw_gab or "Indisponível"
            acertou = clean_escolha.lower() in raw_gab.lower()
        return acertou, escolha_label, gabarito_label

    elif tipo == "multipla":
        escolha_label = f"Alternativa {clean_escolha}"
        gab_letter = extract_multiple_choice_letter(raw_gab)
        if gab_letter:
            gabarito_label = f"Alternativa {gab_letter}"
            acertou = (clean_escolha == gab_letter)
        else:
            gabarito_label = raw_gab or "Indisponível"
            acertou = f"({clean_escolha})" in raw_gab or f"letra {clean_escolha.lower()}" in raw_gab.lower()
        return acertou, escolha_label, gabarito_label

    # Aberta
    acertou = clean_escolha.lower() in raw_gab.lower()
    return acertou, escolha, raw_gab
