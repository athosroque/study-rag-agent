#!/usr/bin/env python3
import os
import sys
import logging
import httpx
import datetime
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Configurar logs
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configurações iniciais
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.exists(os.path.join(PROJECT_DIR, ".env")):
    load_dotenv(os.path.join(PROJECT_DIR, ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "seu_token_aqui":
    logger.error("A variável TELEGRAM_BOT_TOKEN não está configurada corretamente no .env!")
    sys.exit(1)


async def check_auth(update: Update) -> bool:
    """Verifica se o usuário está autorizado a interagir com o bot."""
    if not ADMIN_CHAT_ID or ADMIN_CHAT_ID == "seu_chat_id_opcional_aqui":
        return True
    
    chat_id = str(update.effective_chat.id)
    if chat_id != str(ADMIN_CHAT_ID):
        logger.warning(f"Usuário não autorizado tentou acessar: {chat_id}")
        await update.effective_message.reply_text("⛔ Acesso não autorizado.")
        return False
    return True


async def fetch_next_revision(categoria: str = None) -> dict:
    """Busca a próxima revisão pendente no backend."""
    async with httpx.AsyncClient() as client:
        try:
            url = f"{API_BASE_URL}/revisoes/pendentes?limite=1"
            if categoria:
                url += f"&categoria={categoria}"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            if not data:
                return None
            return data[0]
        except Exception as e:
            logger.error(f"Erro ao buscar revisões: {e}")
            return None


async def send_revision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia a próxima revisão para o usuário."""
    categoria = context.user_data.get("categoria_ativa", "questao")
    item = await fetch_next_revision(categoria)
    
    chat_id = update.effective_chat.id
    if not item:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎉 Tudo em dia! Você não tem revisões pendentes no momento."
        )
        return

    materia = item.get("materia_nome") or "Geral"
    topico = item.get("topico", "")
    conteudo = item.get("conteudo", "")
    revisao_id = item.get("revisao_id")
    atrasada = "⚠️ ATRASADA\n" if item.get("atrasada") else ""

    texto = f"{atrasada}📚 *{materia}* — {topico}\n\n{conteudo}"
    
    # Armazena a resposta no context data para mostrar quando ele clicar
    context.chat_data[f"rev_{revisao_id}"] = item.get("detalhes_resposta") or "Sem detalhes adicionais."

    keyboard = [
        [InlineKeyboardButton("Mostrar Resposta 👁️", callback_data=f"ans_{revisao_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=chat_id,
        text=texto,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def handle_revisar_comando(update: Update, context: ContextTypes.DEFAULT_TYPE, categoria: str, msg: str):
    """Handler genérico para comandos de revisão."""
    if not await check_auth(update):
        return
    context.user_data["categoria_ativa"] = categoria
    await update.message.reply_text(msg, parse_mode="Markdown")
    await send_revision(update, context)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o comando /start e /revisar (foca em questões)."""
    await handle_revisar_comando(update, context, "questao", "👋 Olá, Hermes! Buscando suas *questões* atrasadas...")

async def teoria_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para revisar teorias."""
    await handle_revisar_comando(update, context, "teoria", "📚 Buscando suas *teorias* atrasadas...")

async def todas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para revisar todas as categorias misturadas."""
    await handle_revisar_comando(update, context, None, "🌪️ Buscando *todas* as suas revisões atrasadas...")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata os cliques nos botões."""
    if not await check_auth(update):
        return

    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("ans_"):
        # Mostrar a resposta
        revisao_id = data.split("_")[1]
        detalhes = context.chat_data.get(f"rev_{revisao_id}", "Detalhes perdidos da memória local.")
        
        texto_resposta = f"💡 *Gabarito/Detalhes:*\n{detalhes}\n\n*Como foi seu desempenho?*"
        
        keyboard = [
            [
                InlineKeyboardButton("❌ Errei (0)", callback_data=f"rate_{revisao_id}_0"),
                InlineKeyboardButton("⚠️ Difícil (1)", callback_data=f"rate_{revisao_id}_1")
            ],
            [
                InlineKeyboardButton("✅ Acertei (2)", callback_data=f"rate_{revisao_id}_2"),
                InlineKeyboardButton("⭐ Fácil (3)", callback_data=f"rate_{revisao_id}_3")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Edita a mensagem anterior adicionando a resposta e trocando os botões
        await query.edit_message_text(
            text=f"{query.message.text}\n\n{texto_resposta}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    elif data.startswith("rate_"):
        # Registrar o desempenho
        _, revisao_id, nota = data.split("_")
        
        async with httpx.AsyncClient() as client:
            try:
                payload = {
                    "feita": True,
                    "revisao_id": int(revisao_id),
                    "desempenho": int(nota)
                }
                resp = await client.post(f"{API_BASE_URL}/revisoes/responder", json=payload)
                resp.raise_for_status()
                
                resultado = resp.json()
                msg = resultado.get("mensagem", "Registrado.")
            except Exception as e:
                logger.error(f"Erro ao registrar resposta: {e}")
                msg = "Erro ao se comunicar com a API."

        # Remove os botões da mensagem que foi respondida
        await query.edit_message_text(
            text=f"{query.message.text}\n\n_Registro: {msg}_",
            reply_markup=None,
            parse_mode="Markdown"
        )
        
        # Manda a próxima revisão!
        await send_revision(update, context)


async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """Envia o resumo matinal às 08:00."""
    if not ADMIN_CHAT_ID or ADMIN_CHAT_ID == "seu_chat_id_opcional_aqui":
        logger.warning("Não há ADMIN_CHAT_ID configurado para o resumo diário.")
        return

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/revisoes/stats")
            resp.raise_for_status()
            stats = resp.json()
        except Exception as e:
            logger.error(f"Erro ao buscar stats matinal: {e}")
            return

    atrasadas = stats.get("atrasadas", 0)
    devidas = stats.get("devidas_hoje", 0) - atrasadas
    total_hoje = atrasadas + devidas

    if total_hoje == 0:
        texto = "🌅 *Bom dia!* O seu cronograma de revisões está vazio para hoje. Descanse ou estude tópicos novos!"
    else:
        texto = f"🌅 *Bom dia!* Aqui está o seu cronograma de revisões de hoje:\n\n"
        texto += f"🔴 *Atrasadas:* {atrasadas}\n"
        texto += f"🟡 *Para hoje:* {devidas}\n"
        texto += f"📊 *Total:* {total_hoje} questões pendentes.\n\n"
        texto += "Vamos bater essa meta?"

    keyboard = [[InlineKeyboardButton("🚀 Iniciar Revisões", callback_data="start_review_todas")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=texto,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def send_evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Envia cobrança noturna às 20:00 caso haja pendências."""
    if not ADMIN_CHAT_ID or ADMIN_CHAT_ID == "seu_chat_id_opcional_aqui":
        return

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/revisoes/stats")
            resp.raise_for_status()
            stats = resp.json()
        except Exception:
            return

    pendentes = stats.get("devidas_hoje", 0)
    if pendentes > 0:
        texto = f"⚠️ *Cobrança Hermes:* Você ainda tem {pendentes} questões pendentes para o dia de hoje!\n\nNão deixe a curva de esquecimento te pegar!"
        keyboard = [[InlineKeyboardButton("🚀 Começar agora", callback_data="start_review_todas")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=texto,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )


def main():
    logger.info("Iniciando o Hermes Telegram Bot...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("revisar", start_command))
    application.add_handler(CommandHandler("revisar_teoria", teoria_command))
    application.add_handler(CommandHandler("revisar_todas", todas_command))
    
    # Tratamento para o botão de Iniciar do Cronograma
    async def inline_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        context.user_data["categoria_ativa"] = None
        await send_revision(update, context)

    application.add_handler(CallbackQueryHandler(inline_start_handler, pattern="^start_review_todas$"))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Configurar Cron Jobs
    tz = datetime.timezone(datetime.timedelta(hours=-3)) # Horário de Brasília
    job_queue = application.job_queue
    
    hora_manha = datetime.time(hour=8, minute=0, tzinfo=tz)
    hora_noite = datetime.time(hour=20, minute=0, tzinfo=tz)
    
    job_queue.run_daily(send_daily_summary, time=hora_manha)
    job_queue.run_daily(send_evening_reminder, time=hora_noite)

    logger.info("Cron Jobs configurados para 08:00 e 20:00 (GMT-3).")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
