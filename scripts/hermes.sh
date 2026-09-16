#!/usr/bin/env bash
# scripts/hermes.sh — Runner do Hermes Agent no contexto do study-rag-agent
# Usa GLM 5.3 Flash via LiteLLM com chave virtual exclusiva e Langfuse

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# Carrega variáveis locais se existirem
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Chave exclusiva LiteLLM para o Hermes Agent
export HERMES_LITELLM_KEY="${HERMES_LITELLM_KEY:-sk-T-arQ7cEWBQo0H6xNkMCmA}"
export OPENROUTER_API_KEY="$HERMES_LITELLM_KEY"
export OPENROUTER_BASE_URL="${LITELLM_HOST_URL:-http://127.0.0.1:4000/v1}"

# Langfuse Observability
export HERMES_LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:-pk-lf-944c3458-c3e3-40fb-b7c0-e15844a2c848}"
export HERMES_LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY:-sk-lf-2d440ba0-ab8f-464f-b739-3994753b057b}"
export HERMES_LANGFUSE_BASE_URL="${LANGFUSE_HOST:-https://cloud.langfuse.com}"

HERMES_BIN="/home/athos/.local/bin/hermes"

if [ ! -x "$HERMES_BIN" ]; then
    echo "Erro: Hermes Agent não encontrado em $HERMES_BIN" >&2
    exit 1
fi

exec "$HERMES_BIN" "$@"
