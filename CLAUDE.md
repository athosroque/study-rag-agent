Template version: 1.2

# Study RAG Agent — LangGraph com Reconciliação Contínua e pgvector

## 1. Identidade

- **ID:** `study-rag-agent`
- **Nome legível:** Study RAG Agent — Curadoria de Conhecimento com LangGraph, pgvector e LiteLLM
- **Guia de referência:** `../agents/Fluxo LangGraph com Busca e Validação de Vídeos.md`
- **Status:** completed
- **Repositório GitHub:** homelab local / a versionar

## 2. Pré-requisitos

- `llm-gateway` `completed` — LiteLLM rodando na porta 4000 na rede `homelab_default`.
- OpenRouter ativo com créditos.
- Langfuse Cloud e/ou LangSmith configurados para observabilidade.

## 3. Arquitetura

O fluxo LangGraph processa transcrições/conteúdos de aulas e atua como um curador contínuo de base de conhecimento:
1. `video_search`: Busca e recebe a aula (ou extrai legendas do YouTube / síntese didática LLM Tier Fast).
2. `validate_video`: Deduplica se o vídeo já foi processado no PostgreSQL.
3. `extraction_agent`: Extrai itens pedagógicos (teoria, sacada, pegadinha, questão) usando LiteLLM tier `fast` (`deepseek-v4-flash`).
4. `retrieve_similar_items`: Localiza o Tópico Mestre mais próximo no pgvector (`search_master_topic`, `parent_id IS NULL`) e recupera o histórico completo anterior de subitens e fragmentos.
5. `reconciliation_agent`: Curador de conhecimento usando LiteLLM tier `mid` (`mimo-v2.5-pro`) com regras estritas:
   - Tópico Mestre existente: descarta teoria básica repetida (`DESCARTAR`), descarta questões já arquivadas (`DESCARTAR`), e vincula novidades atômicas ao mestre (`ADICIONAR_FRAGMENTO`).
   - Tópico Inédito: cria novo Tópico Mestre (`CRIAR_NOVO`) e vincula os subitens da aula a ele.
6. `db_writer`: Executa persistência relacional com `parent_id` e array JSONB `fragmentos` com embeddings vetoriais individualizados de 384 dimensões (`bge-small-en-v1.5`), além de gravar a transcrição completa na tabela `videos_processados`.

Serving via FastAPI com Server-Sent Events (SSE) e Dashboard Web interativo com cartões hierárquicos e seções didáticas especializadas (Macetes, Pegadinhas e Questões com gabarito retrátil).

## 4. Stack fixada

- Python 3.12+
- LangGraph, LangChain Core, LangChain OpenAI
- PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`)
- LiteLLM Gateway (OpenRouter)
- Langfuse e LangSmith (Observabilidade e Tracing)
- FastAPI, Uvicorn, SSE-Starlette
- Pytest (27 testes unitários e de integração)

## 5. Progresso

- [x] Fase 0 — Especificação e reconciliação concebida
- [x] Fase 1 — Setup de infraestrutura (Docker Compose, PostgreSQL pgvector, rede)
- [x] Fase 2 — Modelos de dados e banco (Pydantic, JSONB fragmentos, busca vetorial)
- [x] Fase 3 — Grafo LangGraph com Reconciliação e observabilidade Langfuse
- [x] Fase 4 — API FastAPI, SSE streaming e Dashboard interativo
- [x] Fase 5 — Transcrições integrais no banco e extração automática de legendas do YouTube
- [x] Fase 6 — Hierarquia Relacional (`parent_id`), Tópicos Mestres, Descarte Semântico de Redundâncias e Acervo Didático em Seções
- [x] Fase 7 — Saneamento de dados legados (`scripts/maintenance/sanitize_hierarchy.py`) e 27 testes automatizados (100% validado)

## 6. Comandos Úteis

```bash
# Subir serviços Docker
docker compose up -d

# Ver logs da API
docker compose logs -f study-rag-api

# Executar testes automatizados (27 testes)
docker exec study-rag-api pytest -v

# Executar saneamento de hierarquia em registros legados
docker exec -i study-rag-api python < scripts/maintenance/sanitize_hierarchy.py

# Testar healthcheck
curl http://localhost:8090/health

# Acessar Dashboard
open http://localhost:8090

# Acessar pgAdmin 4 (Interface Web do Banco de Dados)
open http://localhost:5050

# Executar Hermes Agent (DeepSeek V4 Pro via LiteLLM)
./scripts/hermes.sh

# Consulta rápida via Hermes Agent
./scripts/hermes.sh chat -q "Sua pergunta aqui"

# Consultar gasto acumulado do Hermes Agent no LiteLLM
curl -s -H "Authorization: Bearer sk-master-5184388f4d1f9ae9299217ce84dccaf0d3bf2c78c5aa0a2db831ad308e82a721" "http://127.0.0.1:4000/key/info?key=sk-21UOyNaNyaSvwc7bvj5Dxg"
```


