# Study RAG Agent — Curadoria Contínua de Conhecimento

> **Plataforma de Curadoria de Estudos com LangGraph, PostgreSQL (`pgvector`), LiteLLM e Observabilidade LangSmith / Langfuse.**

O **Study RAG Agent** é um sistema inteligente concebido para transformar aulas e transcrições em uma base de conhecimento atômica, hierarquizada e estritamente **não-redundante** para preparação em concursos e estudos avançados.

---

## 🎯 Showcase Técnico: Competências em Dados, IA e MLOps

### 🤖 Inteligência Artificial & Engenharia de Agentes
- **Orquestração de Agentes (Stateful Agents):** Uso do **LangGraph** para criar um pipeline cíclico de 8 estágios autônomos, gerenciando grafos de estado, memória e tomada de decisão complexa entre os nós.
- **Engenharia de Prompt Avançada & Structured Outputs:** Utilização de `with_structured_output` aliado a esquemas estritos do **Pydantic** para extração precisa de dados não-estruturados, garantindo saídas previsíveis, determinísticas e prevenindo alucinações.
- **Sistemas RAG Avançados e Curadoria Ativa:** Em vez de realizar Retrieval-Augmented Generation ingênuo (injetando fragmentos cegamente no prompt), o sistema age como um curador autônomo, construindo e reconciliando uma ontologia hierarquizada e estritamente não-redundante.
- **Tratamento de Edge Cases de LLMs:** Prevenção ativa de anomalias conhecidas da área, como *Runaway Generation* (loops infinitos de inferência até esgotar limites de API), lidando estruturalmente com as restrições e conflitos nativos de *Function Calling*.

### 📊 Engenharia de Dados & Bancos de Dados Vetoriais
- **Armazenamento Híbrido Avançado:** Modelagem complexa e poliglota dentro de um único SGBD, fundindo características de banco relacional (integridade referencial com chaves como `parent_id`), documento (arrays e objetos `JSONB` para consultas rápidas de consolidação) e vetorial (`vector(384)` para o RAG).
- **Busca Semântica Otimizada com `pgvector`:** Implementação de buscas semânticas escaláveis no PostgreSQL com índices de alta performance **HNSW** (`vector_cosine_ops`), dispensando dependências de APIs pagas utilizando modelos de *embedding* abertos rodando localmente (ex: `BAAI/bge-small-en-v1.5` carregado via **FastEmbed**).
- **Pipelines ETL Robustos:** Ingestão agnóstica de aulas e áudios, seguida por sanitização profunda de texto via Expressões Regulares (Regex), removendo metadados espúrios de transcrições e aplicando algoritmos de *Chunking Semântico com Overlap* (preservando o fechamento de frases e parágrafos estruturais para evitar perda de semântica vetorial).

### ⚙️ MLOps, FinOps & Observabilidade
- **LLM Gateway & Práticas FinOps:** Operação em malha com o **LiteLLM Gateway** para padronização unificada das APIs de inferência. Implementação de fallback dinâmico entre modelos, rastreamento analítico em tempo real do consumo de tokens e segregação rigorosa de orçamento através de *Virtual Keys* geradas por agente (permitindo interrupção instantânea em picos de gasto indesejado).
- **Otimização Extrema de Latência e Custos:** Design engenhoso nas etapas de curadoria/reconciliação delegando o texto pesado à memória da aplicação Python e transitando com o LLM apenas os IDs de referência indexados num array JSON (economia comprovada de até 80% do consumo em *Output Tokens* do modelo).
- **Dual-Tracing de Interações AI:** Visibilidade granular end-to-end das correntes de raciocínio lógico (CoT) instrumentadas simultaneamente via **LangSmith** e **Langfuse**, mensurando latência por nó, avaliação de custos da API e monitoramento analítico de *LLM Traces*.

### 🚀 Engenharia de Software Aplicada
- **Arquitetura Orientada a Serviços (SOA) e Assíncrona:** Backend desenvolvido com **FastAPI** provendo *Streaming Server-Sent Events* (SSE) que repassa fluxos de pensamento dos agentes em tempo real à UI. Modelagem assíncrona robusta rodando isoladamente os *Workers* de Curadoria e Bots de Automação (Telegram).
- **Algoritmos Aplicados a Produtos de Dados:** Criação nativa de um motor analítico (SQL/Python) de repetição espaçada modelado a partir da matemática da *Curva do Esquecimento de Ebbinghaus*, funcionando como um robusto sistema reativo de recomendações pedagógicas ativas.
- **Confiabilidade, Integração Contínua e DevOps:** Garantia de qualidade por meio de uma robusta suíte de mais de 60 testes automatizados baseados em **pytest** cobrindo inteiramente os fluxos críticos lógicos e orquestração determinística, empacotada em arquitetura conteinerizada padronizada com **Docker** e **Docker Compose**.

---

## 🏛️ Arquitetura e Paradigma de Conhecimento

O sistema resolve o problema clássico de inchaço de dados em RAGs educacionais (*data bloat* e fragmentação desordenada) através de um modelo híbrido **Relacional + Vetorial + JSONB**:

```mermaid
flowchart TD
    A[Nova Aula / Transcrição] --> B[video_search: Recepção ou Extração de Legenda]
    B --> C[validate_video: Checagem de Duplicidade do Vídeo]
    C --> D[extraction_agent Tier Fast: Extração de Conceitos, Macetes, Pegadinhas e Questões]
    D --> E[retrieve_similar_items: Busca Semântica pgvector de Tópico Mestre]
    
    E --> F{Tópico Mestre já existe no Banco?}
    
    F -- SIM (ex: ID 1) --> G[Carrega Tópico ID 1 + Todas suas Pegadinhas e Questões Anteriores]
    G --> H[reconciliation_agent Tier Mid: Curadoria de Não-Redundância]
    H --> I1[Teoria Básica Repetida: DESCARTAR]
    H --> I2[Questão ou Pegadinha Repetida: DESCARTAR]
    H --> I3[Nova Questão / Macete Inédito: ADICIONAR_FRAGMENTO vinculado ao ID 1]
    
    F -- NÃO (Inédito) --> J[CRIAR_NOVO: Novo Tópico Mestre parent_id=NULL]
    J --> K[Vincula subitens da aula ao Novo ID Mestre gerado]
    
    I3 --> L[db_writer: Gravação Relacional parent_id + JSONB + Embeddings]
    K --> L
    L --> M[PostgreSQL 16 + pgvector: Base Hierarquizada Pronta para Busca]
```

### Princípios Fundamentais

1. **Tópico Mestre (`parent_id IS NULL`):** Representa o núcleo temático de uma matéria (ex: *"Atributos dos Atos Administrativos"*, *"Conceito de Tabela Verdade e Equivalências"*).
2. **Subitens Atômicos Relacionais (`parent_id = master_id`):**
   - 💡 **Macetes e Sacadas:** Ferramentas mnemônicas e atalhos cognitivos (ex: *PATI*, *MANÉ*, *NEouMA*).
   - ⚠️ **Pegadinhas de Prova:** Exceções contra-intuitivas e armadilhas recorrentes de bancas examinadoras (ex: *ausência de autoexecutoriedade na cobrança de multas*).
   - ❓ **Questões de Fixação:** Enunciados comentados com gabarito explicativo para autoavaliação.
3. **Embeddings Individuais:** Cada subitem possui seu próprio vetor matemático de 384 dimensões (`bge-small-en-v1.5`), permitindo que a busca semântica recupere tanto o tópico amplo quanto a pegadinha cirúrgica.
4. **Dupla Persistência (Relacional + JSONB):** O registro mestre mantém os subitens como linhas com `parent_id` (para integridade referencial e buscas granulares) e simultaneamente em seu array JSONB `fragmentos` (para leitura consolidada e consultas eficientes via API e Telegram Bot).

---

## 📂 Estrutura do Projeto

O projeto foi refatorado para organizar logicamente o *core* da aplicação, o bot autônomo e os scripts auxiliares:

```text
study-rag-agent/
├── src/                        # 🧠 Aplicação Central (RAG, Agentes e API)
│   ├── api.py                  # Endpoints FastAPI
│   ├── graph.py                # Pipeline LangGraph (Orquestração dos Agentes)
│   ├── db.py                   # Consultas PostgreSQL (pgvector) e Relacional
│   ├── revisoes.py             # Motor de Repetição Espaçada (Curva de Ebbinghaus)
│   ├── embeddings.py           # Configuração de Vetores (FastEmbed/BGE)
│   ├── llm.py                  # Integração com LiteLLM
│   ├── models.py               # Schemas Pydantic (Validação Estruturada)
│   └── config.py               # Variáveis de ambiente
├── src/bot/                    # 🤖 Interface Conversacional e Autônoma
│   └── main.py                 # Bot do Telegram (Jobs, Notificações e Handlers)
├── scripts/                    # 🛠️ Utilitários de CLI
│   ├── hermes.sh               # Ferramenta CLI de auxílio com IA local
│   └── maintenance/            # Scripts para intervenções no Banco de Dados
│       ├── backfill_revisoes.py
│       ├── clean_db.py
│       ├── migrate_gabarito_column.py
│       └── sanitize_hierarchy.py
├── tests/                      # 🧪 Testes Automatizados (pytest)
├── dossies/                    # 📄 Ingestão de materiais em lote
├── docker-compose.yml          # Topologia Integrada (DB, API, Bot)
└── Dockerfile                  # Imagem Base (Python 3.12-slim)
```

---

## 🗄️ Estrutura do Banco de Dados (PostgreSQL 16 + pgvector)

### Tabela `itens_estudo`
| Coluna | Tipo | Descrição |
| :--- | :--- | :--- |
| `id` | `SERIAL PRIMARY KEY` | Identificador único do item |
| `topico` | `TEXT` | Assunto ou título do item |
| `categoria` | `TEXT` | `teoria`, `sacada`, `pegadinha` ou `questao` |
| `conteudo` | `TEXT` | Estritamente o enunciado/assertiva (sem gabarito) ou conceito/macete |
| `gabarito` | `TEXT` | Resposta objetiva ou gabarito oficial direto (ex: 'CERTO', 'ERRADO', alternativa ou resposta concisa) |
| `detalhes_resposta` | `TEXT` | Resolução comentada, fundamentação técnica ou notas pedagógicas |
| `fragmentos` | `JSONB` | Array consolidado de fragmentos e subitens vinculados |
| `parent_id` | `INTEGER REFERENCES itens_estudo(id)` | Chave estrangeira autoreferencial para o Tópico Mestre |
| `materia_id` | `INTEGER REFERENCES materias(id)` | Chave estrangeira para a disciplina na tabela `materias` |
| `embedding` | `vector(384)` | Vetor denso indexado via **HNSW** (`vector_cosine_ops`) |
| `qtd_revisoes` | `INTEGER` | Quantidade de enriquecimentos e revisões do tópico |
| `link_do_video` | `TEXT` | URL da aula ou fonte de origem |
| `criado_em` | `TIMESTAMP` | Data de criação original |
| `data_ultima_revisao`| `TIMESTAMP` | Data da última novidade integrada |

### Tabela `revisoes`
| Coluna | Tipo | Descrição |
| :--- | :--- | :--- |
| `id` | `SERIAL PRIMARY KEY` | Identificador único da revisão |
| `item_id` | `INTEGER REFERENCES itens_estudo(id)` | Item de estudo sendo revisado |
| `etapa` | `INTEGER` | Passo atual na escada de intervalos |
| `intervalo_dias` | `INTEGER` | Dias de intervalo até a próxima revisão |
| `data_agendada` | `TIMESTAMP` | Quando a revisão deve ser feita |
| `status` | `TEXT` | `pendente`, `adiada`, `feita` ou `concluida` |
| `desempenho` | `INTEGER` | Autoavaliação (0=Errei a 3=Fácil) |

### Tabela `videos_processados`
| Coluna | Tipo | Descrição |
| :--- | :--- | :--- |
| `link_do_video` | `TEXT PRIMARY KEY` | URL ou slug da aula processada |
| `tema_busca` | `TEXT` | Assunto de pesquisa utilizado |
| `titulo` | `TEXT` | Título legível da aula |
| `transcricao_completa`| `TEXT` | Texto integral da transcrição (YouTube ou síntese LLM) |
| `data_processamento`| `TIMESTAMP` | Momento da ingestão |

---

## 🔄 Grafo LangGraph de 8 Estágios

O pipeline é orquestrado de forma assíncrona com streaming Server-Sent Events (SSE):

| Estágio | Nó | Papel & Modelo |
| :--- | :--- | :--- |
| 1 | `video_search` | Obtém transcrição de URL do YouTube via `youtube-transcript-api` ou sintetiza aula profunda via LLM (`glm-5.3-flash`) se nenhum texto for fornecido. |
| 2 | `validate_video` | Consulta `videos_processados` para prevenir reprocessamento acidental de links idênticos. |
| 3 | `extraction_agent` | LLM (`glm-5.3-flash`) extrai itens estruturados com separação estrita: `conteudo` (apenas enunciado/pergunta), `gabarito` (resposta objetiva) e `detalhes_resposta` (resolução comentada). |
| 4 | `retrieve_similar_items` | Consulta pgvector para ancoragem no Tópico Mestre (`search_master_topic`) e carrega todo o acervo histórico anterior particionado por matéria. |
| 5 | `reconciliation_agent` | LLM (`glm-5.3-flash`) atua como Curador-Chefe comparando candidato a candidato: descarta redundâncias e vincula novidades ao mestre. |
| 6 | `db_writer` | Persiste vídeos, insere novos tópicos mestres, anexa linhas filhas com `parent_id`, propaga `gabarito` e atualiza JSONB. |
| 7 | `cespe_agent` | Itera sobre todos os tópicos extraídos da aula e formula dinamicamente questões inéditas (nível médio/alto) no padrão CESPE/Cebraspe focando em pegadinhas (`glm-5.3-flash`). |
| 8 | `revisao_scheduler` | Agenda o ciclo de revisões ativas exclusivamente para questões (`categoria='questao'`) no motor de repetição espaçada (Curva de Ebbinghaus). |

---

## 🧠 Motor de Revisão Espaçada (Curva de Ebbinghaus)

O projeto integra um motor nativo de repetição espaçada projetado para combater a Curva do Esquecimento. A revisão ativa é focada estritamente em **questões com gabarito**, enquanto os tópicos conceituais (teorias, sacadas e pegadinhas) ficam permanentemente acessíveis sob demanda.

### A Lógica da Escada
- **Intervalos Padrão:** `[1, 7, 15, 30]` dias (configurável via `REVISAO_INTERVALOS_DIAS` no `.env`).
- **Autoavaliação e Avanço:**
  - **Errei (0):** Volta para a etapa inicial (0) e reprograma para amanhã (+1 dia).
  - **Difícil (1):** Repete a etapa atual (o intervalo não avança).
  - **Acertei (2) / Fácil (3):** Avança para o próximo degrau da escada (+7d ➔ +15d ➔ +30d).
- **Adiantamento Seguro:** Se uma revisão não for feita no dia agendado, o sistema a marca como `adiada` e a empurra para o dia seguinte, sem punir a etapa do usuário no ciclo de retenção.
- Concluída a etapa de 30 dias com sucesso, o ciclo do item é encerrado (assumido como consolidado na memória longa).

---

## 🤖 Agente Cobrador Ativo (Integração Telegram)

Para evitar que o motor de Ebbinghaus dependa da lembrança do usuário de abrir o sistema, construímos um **Telegram Bot Interativo (`study-rag-bot`)** com autonomia temporal.

### Autonomia e Cron Jobs
Rodando em seu próprio container Docker (`main.py`), o bot utiliza o `JobQueue` para efetuar cobranças ativas integradas à API:
1. **Resumo Matinal (08:00 GMT-3):** O bot consome a rota `/api/v1/revisoes/stats?categoria=questao` e envia o cronograma diário de questões, listando itens atrasados e devidos.
2. **Cobrança Noturna (20:00 GMT-3):** Caso existam questões pendentes no fim do dia, o bot envia alertas ostensivos com botão "call-to-action" para zerar as pendências.

### Comandos e Interatividade Completa
- `/revisar` (ou `/start`): Inicia a fila de **Questões** pendentes/atrasadas com simulador ativo interativo.
- `/teoria [assunto]`: Consulta o resumo teórico sob demanda para qualquer matéria/assunto (ex: `/teoria Crase`, `/teoria Atos Administrativos`).
- **Mensagens Livres:** Envio de mensagens de texto espontâneas (ex: "teoria de crase") ativa a busca conceitual e entrega os fundamentos sem poluir a fila de revisões.
- **Botões Inline Interativos (Simulador Ativo):**
  - **Estilo CESPE / Cebraspe (Certo / Errado):** Renderiza botões `[ 🟢 Certo ]` e `[ 🔴 Errado ]`.
  - **Múltipla Escolha (A..E):** Renderiza botões `[ A ] [ B ] [ C ] [ D ] [ E ]`.
  - **Correção Automática Instantânea:** Ao tocar na alternativa, o bot avalia a resposta contra o gabarito oficial:
    - **Acertou:** Grava `desempenho = 2`, avança a etapa da curva (+7d, +15d, +30d), exibe `🎉 Você ACERTOU!` e apresenta a fundamentação comentada.
    - **Errou:** Grava `desempenho = 0`, reinicia o ciclo, reprograma a questão para o dia seguinte (+1d) e exibe `❌ Você ERROU!` com o gabarito comentado e pegadinhas explicadas.
  - **Controle Pós-Resposta:**
    - `[ ➡️ Próxima Questão ]`: Carrega instantaneamente a próxima pendência.
    - `[ ⚠️ Acertei no Chute (Difícil) ]`: Permite rebaixar para dificuldade 1 caso tenha acertado com dúvida, evitando avanço precoce.
  - `[📖 Ver Teoria]`, `[💡 Ver Sacada]`, `[⚠️ Ver Pegadinha]`: Abrem cards conceituais complementares vinculados diretamente à questão.
  - `[ 👁️ Ver Gabarito Direto ]`: Fallback para visualização direta e questões abertas/conceituais.

---

## 📡 Interfaces de Acesso e Consumo

A interação com a base de conhecimento e revisões ocorre através de:

1. **🤖 Bot Autônomo do Telegram:** 
   - Envio diário de questões com base na Curva do Esquecimento (Ebbinghaus em `1, 7, 15, 30` dias).
   - Resolução interativa via botões (C/E e Múltipla Escolha) com correção automática instantânea.
   - Apoio conceitual sob demanda (`[📖 Ver Teoria]`, `[💡 Ver Sacada]`, `[⚠️ Ver Pegadinha]`).
   - Reagendamento automático de acertos/erros no banco de dados.

2. **⚡ API REST FastAPI (`http://localhost:8090`):**
   - Documentação interativa Swagger acessível em `http://localhost:8090/docs`.
   - Ingestão e curadoria via streaming SSE (`/api/v1/process/stream`).
   - Busca semântica vetorial pgvector (`/api/v1/search`).
   - Apoio conceitual para questões (`/api/v1/items/{id}/relacionados`).
   - Gerenciamento de ciclo de repetição espaçada e dados de estudo.

---

## 🚀 Como Executar

### Pré-requisitos
- Docker & Docker Compose
- LiteLLM Gateway rodando na rede `homelab_default` (porta 4000)

### Inicialização
```bash
# Subir os contêineres do banco (pgvector), API FastAPI e Telegram Bot
docker compose up -d

# Verificar logs da API
docker compose logs -f study-rag-api

# Executar a suíte completa de testes automatizados (64 testes)
docker exec study-rag-api pytest -v
```

### Endpoints da API REST
- `GET /health` — Status de integridade e conectividade com banco e modelos.
- `GET /api/v1/stats` — Contadores consolidados de tópicos mestres, fragmentos e vídeos.
- `GET /api/v1/items` — Listagem de tópicos (parâmetro `only_parents=true` por padrão).
- `GET /api/v1/items/{id}` — Detalhes completos do item, incluindo filhos relacionais e fragmentos.
- `GET /api/v1/items/{id}/relacionados` — Retorna os itens conceituais vinculados (`teoria`, `sacada`, `pegadinha`) para suporte à resolução de questões.
- `GET /api/v1/materias` — Listagem de disciplinas normalizadas com contadores de tópicos e itens.
- `POST /api/v1/search` — Busca vetorial semântica direta no PostgreSQL.
- `POST /api/v1/process` — Processamento síncrono de aula pelo LangGraph.
- `GET /api/v1/process/stream` — Streaming SSE da curadoria nó a nó em tempo real.
- `GET /api/v1/videos` — Lista de aulas e metadados de transcrição.
- `GET /api/v1/videos/detail` — Transcrição completa de uma aula.

### Endpoints de Revisões
- `GET /api/v1/revisoes/stats` — Contadores e painel geral de devidas, atrasadas e próximas (filtro opcional `categoria`, padrão `questao`).
- `GET /api/v1/revisoes/pendentes` — Fila filtrável de revisões em aberto (filtro opcional `categoria`, padrão `questao`).
- `GET /api/v1/revisoes/plano` — Distribuição diária do horizonte futuro.
- `POST /api/v1/revisoes/responder` — Transação central que registra a nota de desempenho, resolve a pendência e insere o próximo agendamento no banco.

---

## 🔭 Observabilidade & Tracing (LangSmith & Langfuse)

O projeto suporta tanto o **LangSmith** quanto o **Langfuse** para rastreamento de LLM calls e nós do LangGraph:

- **LangSmith (Nativo LangChain/LangGraph):**
  Defina as variáveis no `.env` ou no ambiente do Docker:
  ```bash
  LANGCHAIN_TRACING_V2=true
  LANGCHAIN_API_KEY=lsv2_pt_...
  LANGCHAIN_PROJECT=study-rag-agent
  LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
  ```
- **Langfuse:**
  ```bash
  LANGFUSE_PUBLIC_KEY=pk-lf-...
  LANGFUSE_SECRET_KEY=sk-lf-...
  LANGFUSE_HOST=https://cloud.langfuse.com
  ```
Ambas as ferramentas podem ser usadas simultaneamente (dual-tracing) ou de forma independente.

---

## 🧪 Testes Automatizados

A suíte em `tests/` cobre 100% dos fluxos essenciais:
- `test_api_videos.py`: Rotas de listagem e detalhe de vídeos processados.
- `test_graph_flow.py`: Normalização de slugs, parsing de URLs do YouTube, geração didática sintética e persistência.
- `test_master_topic_hierarchy.py`: Ancoragem de tópicos mestres, descarte de redundância e validação de `parent_id`.
- `test_models.py`: Serialização e validação dos esquemas Pydantic.
- `test_reconciliation.py`: Tomadas de decisão do agente curador (`CRIAR_NOVO`, `ADICIONAR_FRAGMENTO`, `DESCARTAR`) e contingência segura.

---

## ⚕ Hermes Agent (GLM 5.3 Flash & LiteLLM FinOps)

O projeto conta com o **Hermes Agent** integrado para assistência autônoma, diagnósticos e automações no workspace:

- **Modelo**: `glm-5.3-flash` / `z-ai/glm-5.3-flash` (via OpenRouter credits).
- **Roteador**: Gateway LiteLLM local (`http://127.0.0.1:4000/v1`).
- **Chave Virtual Exclusiva Ativa**: `vk-hermes-agent-glm-5.3-flash` (`sk-T-arQ7cEWBQo0H6xNkMCmA`) — isolamento estrito de orçamento e custos.
- **Observabilidade**: Todas as chamadas geram traces individuais no **Langfuse** com tags do projeto e do agente.

### Comandos:
```bash
# Chat interativo com Hermes no contexto do projeto:
./scripts/hermes.sh

# Consulta rápida (one-shot):
./scripts/hermes.sh chat -q "Explique a reconciliação deste agente"

# Verificar status e dependências:
./scripts/hermes.sh doctor

# Consultar gasto acumulado do Hermes Agent no LiteLLM:
curl -s -H "Authorization: Bearer sk-master-5184388f4d1f9ae9299217ce84dccaf0d3bf2c78c5aa0a2db831ad308e82a721" \
  "http://127.0.0.1:4000/key/info?key=sk-T-arQ7cEWBQo0H6xNkMCmA" | jq .info
```

---

## 🔑 Guia de Gestão e Troca de Virtual Keys (FinOps & Monitoramento)

Este roteiro serve como referência para criar, rotacionar e auditar as Virtual Keys no LiteLLM Gateway, tanto para o **Hermes Agent** quanto para os **agentes do pipeline RAG** e outros serviços do homelab.

### 1. Como Criar uma Nova Virtual Key no LiteLLM

Para criar uma chave exclusiva com orçamento e modelos permitidos:

```bash
curl -s -X POST "http://127.0.0.1:4000/key/generate" \
  -H "Authorization: Bearer sk-master-5184388f4d1f9ae9299217ce84dccaf0d3bf2c78c5aa0a2db831ad308e82a721" \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["z-ai/glm-5.3-flash", "glm-5.3-flash"],
    "metadata": {
      "project": "study-rag-agent",
      "agent": "hermes",
      "model_intent": "glm-5.3-flash"
    },
    "key_alias": "vk-hermes-agent-glm-5.3-flash"
  }' | jq .
```
> O retorno conterá o token no campo `"key"` (ex: `sk-...`). Guarde este valor, pois ele só é exibido na criação.

---

### 2. Onde Atualizar a Chave do Hermes Agent

Ao rotacionar ou trocar a chave do Hermes, atualize os seguintes locais:

1. **Configuração Global do Hermes (`~/.hermes/.env`)**:
   ```bash
   HERMES_LITELLM_KEY=sk-NOVA_CHAVE
   OPENROUTER_API_KEY=sk-NOVA_CHAVE
   ```

2. **Modelo Padrão do Hermes (`~/.hermes/config.yaml`)**:
   ```yaml
   model:
     base_url: http://127.0.0.1:4000/v1
     default: glm-5.3-flash
     provider: litellm

   custom_providers:
   - base_url: http://127.0.0.1:4000/v1
     key_env: HERMES_LITELLM_KEY
     model: glm-5.3-flash
     name: litellm
   ```

3. **Ambiente do Repositório (`study-rag-agent/.env`)**:
   ```bash
   HERMES_LITELLM_KEY=sk-NOVA_CHAVE
   ```

4. **Script de Inicialização (`scripts/hermes.sh`)**:
   ```bash
   export HERMES_LITELLM_KEY="${HERMES_LITELLM_KEY:-sk-NOVA_CHAVE}"
   ```

---

### 3. Onde Atualizar as Chaves dos Demais Agentes do Pipeline

Para alterar a chave dos nós do LangGraph ou de outros agentes:

1. **Pipeline de Estudos (`study-rag-agent/.env`)**:
   ```bash
   # Chave que autentica as chamadas do LangGraph no LiteLLM
   LITELLM_API_KEY=sk-SUA_VIRTUAL_KEY_PIPELINE
   ```
   *Se usar uma virtual key dedicada para o pipeline em vez da master key, configure os modelos permitidos (`["fast", "mid", "strong"]`) na criação da chave.*

2. **Configuração de Modelos no Gateway LiteLLM (`/home/athos/llm-gateway/config.yaml`)**:
   - `DEFAULT_LLM_MODEL`: `glm-5.3-flash` (unificado para todos os agentes: extração, reconciliação e questões Cespe)
   - Aliases adicionais suportados: `z-ai/glm-5.3-flash`, `deepseek-v4.1-flash`, `strong` (`z-ai/glm-5.2`)
   *Para adicionar novos modelos ao gateway, edite o `config.yaml` do `llm-gateway` e reinicie o container: `docker restart litellm`.*

### 3.1. Compressão Automática de Prompts (Headroom)

O projeto suporta a interceptação e compressão automática de prompts massivos (como transcrições completas) de forma totalmente transparente através do proxy **Headroom**. 

Para ativar a compressão em todas as requisições do RAG:
1. A virtual key criada no gateway LiteLLM deve possuir estritamente o alias `vk-headroom`.
2. Configure a chave gerada no `.env` do projeto central:
   ```bash
   LITELLM_API_KEY=sk-CHAVE_GERADA_COM_ALIAS_VK_HEADROOM
   ```
O interceptador (configurado no Gateway) vai capturar as chamadas e reduzir drasticamente o tamanho dos textos antes de enviar ao modelo (resultando em inferências até 4x mais rápidas na extração de tópicos da transcrição).

### 3.2. Otimização de Tokens de Saída (Reconciliador)

O **Agente Reconciliador** analisa subitens extraídos para decidir se devem ser descartados (redundância) ou salvos no Tópico Mestre.
Para evitar que o LLM gaste milhares de tokens reescrevendo o texto de cada item apenas para informar sua decisão, o fluxo do LangGraph foi otimizado estruturalmente:
- O agente envia ao LLM os candidatos numerados com um `indice_candidato`.
- O LLM responde com um JSON extremamente enxuto contendo apenas o `indice_candidato`, a ação escolhida e a justificativa.
- O código em Python remonta os dados mapeando os índices de volta para os objetos originais armazenados em memória. Essa técnica despenca o consumo de Output Tokens do modelo em cerca de 80% e extingue totalmente a chance de o JSON quebrar por estourar o limite de 4096 tokens da API.

---

### 4. Monitoramento e Auditoria de Consumo (FinOps)

#### Listar todas as chaves ativas:
```bash
curl -s -H "Authorization: Bearer sk-master-5184388f4d1f9ae9299217ce84dccaf0d3bf2c78c5aa0a2db831ad308e82a721" \
  "http://127.0.0.1:4000/key/list" | jq .
```

#### Consultar consumo em tempo real de uma chave:
```bash
curl -s -H "Authorization: Bearer sk-master-5184388f4d1f9ae9299217ce84dccaf0d3bf2c78c5aa0a2db831ad308e82a721" \
  "http://127.0.0.1:4000/key/info?key=sk-T-arQ7cEWBQo0H6xNkMCmA" | jq .info
```
*Campos principais retornados:*
- `spend`: Total acumulado em USD ($).
- `models`: Lista de modelos autorizados.
- `last_active`: Timestamp da última requisição.
- `metadata`: Metadados do projeto e agente.

#### Observabilidade no Langfuse:
- Acesse [Langfuse Cloud](https://cloud.langfuse.com).
- Filtre por:
  - `tags` ou `metadata.project`: `study-rag-agent`
  - `metadata.agent`: `hermes`
  - `key_alias`: `vk-hermes-agent-glm-5.3-flash`


