# Aprendizado sobre Prompts no LangChain e LiteLLM (study-rag-agent)

Este arquivo foi criado para registrar uma lição aprendida durante o desenvolvimento deste agente de estudos.

## Problema de Runaway Generation (Loop Infinito)

**Contexto**: O projeto utiliza `llm.with_structured_output(...)` do LangChain com modelos roteados via LiteLLM (ex: `glm-5.3-flash`, Gemini 1.5, etc.).
**O que ocorreu**: Dois agentes consumiram extatos 128.000 *completion tokens* (limite absoluto de saída do modelo) e geraram custos altíssimos.
**Causa Raiz**: Ocorreu um conflito entre a chamada estruturada de função (*Function Calling*) do LangChain e instruções estritas no *System Prompt*.

### Anti-Padrão (O que NUNCA fazer)
Nunca combine `with_structured_output` com instruções no prompt que:
1. Obriguem a resposta a ser estritamente um JSON no corpo do texto (ex: `"A resposta DEVE ser estritamente um JSON válido."`).
2. Proíbam a utilização de Markdown (ex: `"NÃO utilize blocos de código Markdown (```json ... ```)"`).

**Por que isso quebra?**
Quando o modelo tenta usar *Function Calling* mas é proibido de usar seus padrões normais de saída (Markdown), ou quando é forçado a gerar JSON no *Message Content* ao invés dos argumentos da ferramenta, ele se confunde. Isso frequentemente faz o modelo perder a capacidade de emitir o token de parada (*stop token*), levando a um loop infinito gerando espaços ou repetindo a mesma estrutura até esgotar o limite da API (128k tokens).

### Solução e Padrão Recomendado (O que fazer)
- Deixe o `with_structured_output` do LangChain fazer o trabalho sujo de formatação de JSON via ferramenta.
- No seu System Prompt, foque apenas em **O QUÊ** extrair, usando orientações pedagógicas limpas (ex: *"Extraia os dados de forma precisa, separando teorias, pegadinhas e sacadas"*).
- Caso tenha uma lógica manual de `fallback` (como um regex para catar JSON de texto cru), **permita o Markdown**. Os modelos são treinados para embrulhar código e JSON em Markdown (` ```json ... ``` `). Proibir isso aumenta a chance de alucinação.

---
**Regra para Agentes IA futuros atuando neste projeto**:
Sempre que você, como Assistente de Código, for escrever ou alterar prompts para agentes dentro deste ecossistema (RAG, Extração, CESPE, Reconciliação), **você está terminantemente proibido de adicionar regras anti-markdown e regras forçando saída de JSON em texto puro** se o agente fizer uso de saídas estruturadas/pydantic via LLM/LangChain.
