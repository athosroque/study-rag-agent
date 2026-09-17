#!/usr/bin/env python3
import os
import sys
import logging
import subprocess
import psycopg2
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(PROJECT_DIR)
for p in [PROJECT_DIR, ROOT_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from src.llm import get_llm
from src.config import settings

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

if os.path.exists(os.path.join(PROJECT_DIR, ".env")):
    load_dotenv(os.path.join(PROJECT_DIR, ".env"))
elif os.path.exists(os.path.join(ROOT_DIR, ".env")):
    load_dotenv(os.path.join(ROOT_DIR, ".env"))

# Direciona o trace do LangSmith para o projeto dedicado do Ops Bot
if os.getenv("LANGCHAIN_OPS_PROJECT"):
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_OPS_PROJECT")

TELEGRAM_OPS_TOKEN = os.getenv("TELEGRAM_OPS_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")

async def check_auth(update: Update) -> bool:
    if not ADMIN_CHAT_ID or ADMIN_CHAT_ID == "seu_chat_id_opcional_aqui":
        return True
    
    chat_id = str(update.effective_chat.id)
    if chat_id != str(ADMIN_CHAT_ID):
        logger.warning(f"Usuário não autorizado tentou acessar: {chat_id}")
        await update.effective_message.reply_text("⛔ Acesso não autorizado.")
        return False
    return True

@tool
def shell_tool(command: str) -> str:
    """Executa um comando bash no ambiente do container e retorna o resultado. 
    Lembre-se que você está dentro do container, e a base de código está em /app.
    A raiz do container tem o diretório src/."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        out = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        return out[:4000] # Limite para não explodir token limit
    except Exception as e:
        return f"Erro ao executar comando: {e}"

@tool
def sql_tool(query: str) -> str:
    """Executa uma consulta ou comando SQL (PostgreSQL) diretamente no banco de dados e retorna o resultado.
    Se não souber as tabelas, consulte o information_schema.tables."""
    try:
        conn = psycopg2.connect(**settings.db_params)
        cur = conn.cursor()
        cur.execute(query)
        
        q_upper = query.strip().upper()
        if q_upper.startswith("SELECT") or q_upper.startswith("WITH") or "RETURNING" in q_upper:
            rows = cur.fetchall()
            conn.commit()
            cur.close()
            conn.close()
            return str(rows)[:4000] # Limite para não explodir
        else:
            conn.commit()
            rowcount = cur.rowcount
            cur.close()
            conn.close()
            return f"Comando executado com sucesso. Linhas afetadas: {rowcount}"
    except Exception as e:
        return f"Erro ao executar SQL: {e}"

def get_ops_agent():
    # Chave virtual exclusiva para o ops-bot no LiteLLM
    ops_litellm_key = os.getenv("LITELLM_OPS_KEY")
    if ops_litellm_key:
        logger.info("Configurando LLM do Ops Bot com chave virtual exclusiva do LiteLLM.")
        llm = ChatOpenAI(
            model=settings.MODEL_STRONG,
            openai_api_base=settings.LITELLM_BASE_URL,
            openai_api_key=ops_litellm_key,
            temperature=0.1,
            max_tokens=4096,
            timeout=120,
        )
    else:
        llm = get_llm(tier="strong")

    tools = [shell_tool, sql_tool]
    
    system_prompt = """Você é o Hermes Ops Bot, um assistente autônomo focado exclusivamente em operações de desenvolvimento e consultas ao banco de dados do projeto de estudos do usuário.
O usuário é um concurseiro, o foco do projeto (Study RAG Agent) é ser didático/pedagógico. Sua função como desenvolvedor e DBA é ajudar a analisar dados, criar relatórios de uso, buscar questões pendentes ou rodar scripts para ajudar no projeto.
Você tem acesso a:
- shell_tool: para rodar comandos bash
- sql_tool: para fazer consultas SQL no banco PostgreSQL do sistema. O banco tem tabelas como itens_conhecimento, sessoes_estudo, revisoes_espacadas, etc.
Sempre que o usuário pedir algo sobre o banco, deduza as tabelas usando o information_schema se necessário, ou faça a query.
Mantenha uma comunicação clara e direta."""
    
    return create_react_agent(llm, tools, prompt=system_prompt)

ops_agent = get_ops_agent()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
        
    user_text = update.effective_message.text
    if not user_text:
        return
        
    loading_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ Processando sua solicitação (executando agente)...")
    
    try:
        config = {
            "configurable": {"thread_id": str(update.effective_chat.id)},
            "run_name": "ops_agent_execution",
            "metadata": {"project": "study-rag-ops-bot", "user_id": str(update.effective_user.id if update.effective_user else "")},
            "tags": ["ops-bot", "telegram"]
        }
        response = ops_agent.invoke({"messages": [("user", user_text)]}, config=config)
        last_msg = response["messages"][-1]
        answer = last_msg.content or "Sem resposta."
        if isinstance(answer, list):
            answer = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in answer)
    except Exception as e:
        logger.error(f"Erro no agente: {e}")
        answer = f"❌ Erro interno ao processar: {e}"
        
    await loading_msg.delete()
    
    # Enviar a resposta em partes se for muito grande
    max_length = 4000
    if len(answer) == 0:
        answer = "Operação concluída sem output visível."
        
    for i in range(0, len(answer), max_length):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=answer[i:i+max_length]
        )

def main():
    if not TELEGRAM_OPS_TOKEN or TELEGRAM_OPS_TOKEN == "seu_token_ops_aqui":
        logger.error("TELEGRAM_OPS_BOT_TOKEN não configurado no .env.")
        sys.exit(1)
        
    application = Application.builder().token(TELEGRAM_OPS_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    logger.info("Iniciando Hermes Ops Bot (Polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
