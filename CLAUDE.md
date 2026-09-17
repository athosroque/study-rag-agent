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
1. `video_search`: Busca e recebe a aula (ou extrai legendas do YouTube / síntese didática com LLM `glm-5.3-flash`).
2. `validate_video`: Deduplica se o vídeo já foi processado no PostgreSQL.
3. `extraction_agent`: Extrai itens pedagógicos (teoria, sacada, pegadinha, questão) usando LiteLLM (`glm-5.3-flash`) com separação estrita: enunciado limpo (`conteudo`), resposta direta (`gabarito`) e justificativas (`detalhes_resposta`).
4. `retrieve_similar_items`: Localiza o Tópico Mestre mais próximo no pgvector (`search_master_topic`, `parent_id IS NULL`) particionado por matéria e recupera o histórico anterior de subitens.
5. `reconciliation_agent`: Curador de conhecimento usando LiteLLM (`glm-5.3-flash`) com regras estritas:
   - Tópico Mestre existente: descarta teoria básica repetida (`DESCARTAR`), descarta questões já arquivadas (`DESCARTAR`), e vincula novidades atômicas ao mestre (`ADICIONAR_FRAGMENTO`).
   - Tópico Inédito: cria novo Tópico Mestre (`CRIAR_NOVO`) e vincula os subitens da aula a ele.
6. `db_writer`: Executa persistência relacional com `parent_id`, propaga `gabarito` e grava no array JSONB `fragmentos` com embeddings vetoriais individualizados de 384 dimensões (`bge-small-en-v1.5`), além de gravar a transcrição na tabela `videos_processados`.
7. `cespe_agent`: Formula questões assertivas inéditas no padrão CESPE/Cebraspe (Certo/Errado) persistindo `gabarito` isolado e focando em pegadinhas (`glm-5.3-flash`).
8. `revisao_scheduler`: Agenda o ciclo de revisões ativas exclusivamente para itens de categoria `questao` no motor de repetição espaçada (Curva de Ebbinghaus).

Serving via FastAPI com Server-Sent Events (SSE) e documentação Swagger (/docs), integrado ao Bot autônomo do Telegram para revisões espaçadas ativas com apoio conceitual sob demanda (`[📖 Ver Teoria]`, `[💡 Ver Sacada]`, `[⚠️ Ver Pegadinha]`).

## 4. Stack fixada

- Python 3.12+
- LangGraph, LangChain Core, LangChain OpenAI
- PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`)
- LiteLLM Gateway (OpenRouter)
- Langfuse e LangSmith (Observabilidade e Tracing)
- FastAPI, Uvicorn, SSE-Starlette
- Pytest (testes unitários e de integração)

## 5. Progresso

- [x] Fase 0 — Especificação e reconciliação concebida
- [x] Fase 1 — Setup de infraestrutura (Docker Compose, PostgreSQL pgvector, rede)
- [x] Fase 2 — Modelos de dados e banco (Pydantic, JSONB fragmentos, busca vetorial)
- [x] Fase 3 — Grafo LangGraph com Reconciliação e observabilidade Langfuse
- [x] Fase 4 — API FastAPI e SSE streaming
- [x] Fase 5 — Transcrições integrais no banco e extração automática de legendas do YouTube
- [x] Fase 6 — Hierarquia Relacional (`parent_id`), Tópicos Mestres, Descarte Semântico de Redundâncias e Acervo Didático em Seções
- [x] Fase 7 — Saneamento de dados legados (`scripts/maintenance/sanitize_hierarchy.py`) e testes automatizados (100% validado)
- [x] Fase 8 — Normalização de Matérias, Coluna `gabarito` dedicada, Separação Estrita de Enunciado/Resposta, Apoio Conceitual Sob Demanda e Migração de Questões Legadas
- [x] Fase 9 — Intervalos de Revisão Customizados ([1, 7, 15, 30] dias) e Botões Interativos no Telegram (C/E Cebraspe, Múltipla Escolha, Avaliação Automática e Reagendamento Ativo de Ebbinghaus)

## 6. Comandos Úteis

```bash
# Subir serviços Docker
docker compose up -d

# Ver logs da API
docker compose logs -f study-rag-api

# Executar testes automatizados (64 testes)
docker exec study-rag-api pytest -v

# Migrar/popular a coluna gabarito em questões existentes
docker exec study-rag-api python3 scripts/maintenance/migrate_gabarito_column.py

# Executar saneamento de hierarquia em registros legados
docker exec -i study-rag-api python < scripts/maintenance/sanitize_hierarchy.py

# Testar healthcheck
curl http://localhost:8090/health

# Acessar Documentação Swagger da API
open http://localhost:8090/docs

# Acessar pgAdmin 4 (Interface Web do Banco de Dados)
open http://localhost:5050

# Executar Hermes Agent (GLM 5.3 Flash via LiteLLM)
./scripts/hermes.sh

# Consulta rápida via Hermes Agent
./scripts/hermes.sh chat -q "Sua pergunta aqui"

# Consultar gasto acumulado do Hermes Agent no LiteLLM
curl -s -H "Authorization: Bearer sk-master-5184388f4d1f9ae9299217ce84dccaf0d3bf2c78c5aa0a2db831ad308e82a721" "http://127.0.0.1:4000/key/info?key=sk-T-arQ7cEWBQo0H6xNkMCmA"

# Acessar LangGraph Studio local / LangSmith Studio
# Conectar no Studio em: http://localhost:2024
open "https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024"
```


