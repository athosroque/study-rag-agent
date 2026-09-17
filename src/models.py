from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class ItemEstudo(BaseModel):
    materia: str = Field(
        default="Geral",
        description="A disciplina ou matéria geral de concurso (ex: 'Língua Portuguesa', 'Direito Constitucional', 'Direito Administrativo')"
    )
    topico: str = Field(description="O assunto ou tópico específico dentro da matéria (ex: 'Crase', 'Controle de Constitucionalidade')")
    categoria: Literal["teoria", "sacada", "pegadinha", "questao"] = Field(
        description="Categoria do item: 'teoria', 'sacada', 'pegadinha' ou 'questao'"
    )
    conteudo: str = Field(description="O conceito, assertiva/enunciado da questão (estritamente SEM o gabarito), sacada ou pegadinha")
    gabarito: Optional[str] = Field(
        None, description="Gabarito oficial ou resposta direta da questão (ex: 'CERTO', 'ERRADO', 'Alternativa B', ou resposta objetiva concisa). Obrigatório para categoria 'questao'."
    )
    detalhes_resposta: Optional[str] = Field(
        None, description="Resolução comentada, fundamentação técnica ou notas pedagógicas adicionais"
    )
    parent_id: Optional[int] = Field(
        None, description="ID do tópico mestre ao qual este item está vinculado"
    )
    materia_id: Optional[int] = Field(
        None, description="ID da matéria na tabela materias"
    )


class MateriaSummary(BaseModel):
    id: int
    nome: str
    slug: str
    qtd_topicos: int = 0
    qtd_itens: int = 0
    criado_em: Optional[str] = None


class ConjuntoItemsExtraidos(BaseModel):
    items: List[ItemEstudo] = Field(
        default_factory=list,
        description="Lista de itens de estudo extraídos do material pedagógico"
    )


class DecisaoIntegracao(BaseModel):
    indice_candidato: int = Field(
        ..., description="Índice numérico do item candidato avaliado na lista (ex: 0, 1, 2)"
    )
    acao: Literal["CRIAR_NOVO", "ADICIONAR_FRAGMENTO", "DESCARTAR"] = Field(
        description=(
            "Ação de integração curada: "
            "CRIAR_NOVO para tópicos inéditos, "
            "ADICIONAR_FRAGMENTO para enriquecer tópico existente, "
            "DESCARTAR para redundâncias sem valor novo."
        )
    )
    item_id_referencia: Optional[int] = Field(
        None, 
        description="ID numérico do registro histórico existente no banco quando acao é ADICIONAR_FRAGMENTO"
    )
    conteudo_incremental: Optional[str] = Field(
        None, 
        description="Trecho ou sacada que representa a novidade pedagógica para enriquecer o tópico referenciado"
    )
    justificativa: str = Field(
        description="Motivo detalhado da decisão para rastreabilidade e observabilidade"
    )
    parent_id: Optional[int] = Field(
        None,
        description="ID do tópico mestre ao qual o item será vinculado"
    )


class RelatorioReconciliacao(BaseModel):
    decisoes: List[DecisaoIntegracao] = Field(
        default_factory=list,
        description="Lista de decisões tomadas para cada candidato extraído"
    )


class FragmentoConhecimento(BaseModel):
    categoria: str
    conteudo_incremental: str
    adicionado_em: str
    link_do_video: Optional[str] = None
    justificativa: Optional[str] = None


class ItemEstudoCompleto(BaseModel):
    id: int
    topico: str
    categoria: str
    conteudo: str
    gabarito: Optional[str] = None
    detalhes_resposta: Optional[str] = None
    fragmentos: List[Dict[str, Any]] = Field(default_factory=list)
    parent_id: Optional[int] = None
    materia_id: Optional[int] = None
    materia_nome: Optional[str] = None
    filhos: List[Dict[str, Any]] = Field(default_factory=list)
    criado_em: str
    data_ultima_revisao: str
    qtd_revisoes: int = 0
    link_do_video: Optional[str] = None
    similarity: Optional[float] = None


class ProcessVideoRequest(BaseModel):
    tema_busca: str = Field(..., description="Tema ou assunto específico de estudo")
    materia: Optional[str] = Field(None, description="Matéria ou disciplina do conteúdo (opcional, inferida automaticamente se omitida)")
    video_link: Optional[str] = Field(None, description="Link do vídeo (opcional)")
    video_titulo: Optional[str] = Field(None, description="Título da aula (opcional)")
    transcricao: Optional[str] = Field(None, description="Transcrição da aula (opcional)")


class SearchQueryRequest(BaseModel):
    query: str = Field(..., description="Texto para busca semântica")
    limit: int = Field(5, ge=1, le=50, description="Quantidade máxima de resultados")
    categoria: Optional[str] = Field(None, description="Filtro opcional por categoria")
    materia_id: Optional[int] = Field(None, description="Filtro opcional por ID da matéria")


class ProcessedVideoSummary(BaseModel):
    link_do_video: str
    tema_busca: Optional[str] = None
    titulo: Optional[str] = None
    tamanho_transcricao: int = 0
    preview_transcricao: Optional[str] = None
    data_processamento: Optional[str] = None


class ProcessedVideoDetail(BaseModel):
    link_do_video: str
    tema_busca: Optional[str] = None
    titulo: Optional[str] = None
    transcricao_completa: Optional[str] = None
    data_processamento: Optional[str] = None


class RevisaoOut(BaseModel):
    revisao_id: int
    item_id: int
    topico: str
    categoria: str
    materia_nome: Optional[str] = None
    etapa: int
    intervalo_dias: int
    data_agendada: Optional[str] = None
    data_realizada: Optional[str] = None
    status: str
    desempenho: Optional[int] = None
    observacao: Optional[str] = None
    atrasada: bool = False
    atrasada_dias: int = 0
    vence_hoje: bool = False
    conteudo: Optional[str] = None
    gabarito: Optional[str] = None
    detalhes_resposta: Optional[str] = None


class RespostaRevisaoRequest(BaseModel):
    feita: bool = Field(True, description="Se a revisão foi realizada")
    revisao_id: Optional[int] = Field(None, description="ID da revisão (tabela revisoes)")
    item_id: Optional[int] = Field(None, description="ID do item de estudo (alternativa ao revisao_id)")
    desempenho: Optional[int] = Field(
        None, ge=0, le=3,
        description="Autoavaliação: 0=não lembrei, 1=com dificuldade, 2=acertei, 3=fácil"
    )
    observacao: Optional[str] = Field(None, description="Anotação livre do usuário")


class RespostaRevisaoLoteRequest(BaseModel):
    respostas: List[RespostaRevisaoRequest] = Field(default_factory=list)


class QuestaoCespe(BaseModel):
    topico: str = Field(description="Subtópico ou aspecto específico avaliado")
    enunciado: str = Field(description="Assertiva no estilo CESPE/Cebraspe para julgamento de CERTO ou ERRADO")
    gabarito: Literal["CERTO", "ERRADO"] = Field(description="Gabarito oficial da assertiva: 'CERTO' ou 'ERRADO'")
    justificativa: str = Field(description="Fundamentação teórica, doutrinária, jurisprudencial ou legal da assertiva")
    pegadinha_explicada: str = Field(description="Explicação detalhada da armadilha, sutileza ou exceção explorada pela banca")


class ConjuntoQuestoesCespe(BaseModel):
    questoes: List[QuestaoCespe] = Field(
        default_factory=list,
        description="Lista de questões inéditas no estilo CESPE/Cebraspe focadas em pegadinhas e pontos não abordados"
    )


class StudyState(TypedDict, total=False):
    tema_busca: str
    materia: Optional[str]
    materia_id: Optional[int]
    video_encontrado: Dict[str, Any]
    video_valido: bool
    raw_text: str
    extracted_items: List[Dict[str, Any]]
    relevant_existing_items: List[Dict[str, Any]]
    reconciliation_decisions: List[Dict[str, Any]]
    master_topic_id: Optional[int]
    active_master_ids: List[int]
    db_status: str
    summary_stats: Dict[str, int]
    cespe_questions: List[Dict[str, Any]]
    revisoes_agendadas: Dict[str, Any]
