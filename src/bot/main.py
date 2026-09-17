#!/usr/bin/env python3
import os
import sys
import re
import logging
import httpx
import datetime
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Garante path para importação dos módulos do projeto
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(PROJECT_DIR)
for p in [PROJECT_DIR, ROOT_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from src.bot.question_helpers import detect_question_type, evaluate_answer
except ImportError:
    from question_helpers import detect_question_type, evaluate_answer

# Configurar logs
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configurações iniciais
if os.path.exists(os.path.join(PROJECT_DIR, ".env")):
    load_dotenv(os.path.join(PROJECT_DIR, ".env"))
elif os.path.exists(os.path.join(ROOT_DIR, ".env")):
    load_dotenv(os.path.join(ROOT_DIR, ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def safe_send_message(bot, chat_id: int, text: str, reply_markup=None, parse_mode: str = "Markdown"):
    """Envia mensagem tentando Markdown primeiro; se falhar no parse, envia sem parse_mode."""
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.warning(f"Falha ao enviar mensagem com parse_mode={parse_mode}: {e}. Enviando texto puro.")
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=None
        )


async def safe_edit_message_text(query, text: str, reply_markup=None, parse_mode: str = "Markdown"):
    """Edita mensagem tentando Markdown primeiro; se falhar no parse, edita sem parse_mode."""
    try:
        return await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.warning(f"Falha ao editar mensagem com parse_mode={parse_mode}: {e}. Editando texto puro.")
        try:
            return await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=None
            )
        except Exception as err2:
            logger.error(f"Erro crítico ao editar mensagem: {err2}")
            return None


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


async def fetch_next_revision(categoria: str = "questao") -> dict:
    """Busca a próxima revisão pendente no backend (por padrão, apenas questões)."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
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


async def fetch_related_items(item_id: int) -> dict:
    """Busca itens conceituais vinculados (teoria, sacada, pegadinha) para uma questão."""
    if not item_id:
        return {}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/items/{item_id}/relacionados")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"Não foi possível buscar itens relacionados para item #{item_id}: {e}")
    return {}


def _build_concept_buttons(item_id: int, rel_data: dict) -> list:
    """Cria a linha de botões para Teoria, Sacada e Pegadinha se existirem."""
    row = []
    if not rel_data:
        return row
    if rel_data.get("teoria"):
        row.append(InlineKeyboardButton("📖 Ver Teoria", callback_data=f"rel_teoria_{item_id}"))
    if rel_data.get("sacada"):
        row.append(InlineKeyboardButton("💡 Ver Sacada", callback_data=f"rel_sacada_{item_id}"))
    if rel_data.get("pegadinha"):
        row.append(InlineKeyboardButton("⚠️ Ver Pegadinha", callback_data=f"rel_pegadinha_{item_id}"))
    return row


def parse_question_payload(conteudo: str, gabarito: str = None, detalhes_resposta: str = None) -> tuple[str, str]:
    """
    Garante que o enunciado da questão nunca vaze a resposta antes da hora.
    Prioriza o campo 'gabarito' isolado e formata os detalhes pedagógicos complementares.
    Se 'conteudo' contiver 'Resposta:' ou 'Gabarito:' residual, isola o enunciado por regex.
    """
    raw_conteudo = (conteudo or "").strip()
    raw_gabarito = (gabarito or "").strip()
    raw_detalhes = (detalhes_resposta or "").strip()

    # 1. Defesa em tempo de execução: se conteudo ainda tiver 'Resposta:' ou 'Gabarito:'
    m = re.search(r'(.*?)(?:\s*(?:Resposta|Gabarito):\s*)(.*)', raw_conteudo, re.DOTALL | re.IGNORECASE)
    if m:
        enunciado = m.group(1).strip()
        resposta_extraida = m.group(2).strip()
        if not raw_gabarito:
            raw_gabarito = resposta_extraida
    else:
        enunciado = raw_conteudo

    # 2. Formata a resposta final revelada
    if raw_gabarito and raw_detalhes and raw_gabarito.lower() not in raw_detalhes.lower():
        meta_patterns = ["questão chave", "questão clássica", "pergunta direta", "questão de fixação"]
        eh_meta = any(p in raw_detalhes.lower() for p in meta_patterns)
        if eh_meta:
            texto_gabarito = raw_gabarito
        else:
            texto_gabarito = f"{raw_gabarito}\n\n💡 {raw_detalhes}"
    elif raw_gabarito:
        texto_gabarito = raw_gabarito
    elif raw_detalhes:
        texto_gabarito = raw_detalhes
    else:
        texto_gabarito = "Gabarito não disponível."

    return enunciado, texto_gabarito


async def send_revision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia a próxima questão para o usuário revisar com botões de apoio conceitual."""
    categoria = context.user_data.get("categoria_ativa") or "questao"
    item = await fetch_next_revision(categoria)
    
    chat_id = update.effective_chat.id
    if not item:
        await safe_send_message(
            bot=context.bot,
            chat_id=chat_id,
            text="🎉 Tudo em dia! Você não tem revisões de questões pendentes no momento."
        )
        return

    materia = item.get("materia_nome") or "Geral"
    topico = item.get("topico", "")
    revisao_id = item.get("revisao_id")
    item_id = item.get("item_id")
    atrasada = "⚠️ ATRASADA\n" if item.get("atrasada") else ""

    # Garante separação estrita entre enunciado e resposta/gabarito
    conteudo, detalhes = parse_question_payload(
        conteudo=item.get("conteudo", ""),
        gabarito=item.get("gabarito"),
        detalhes_resposta=item.get("detalhes_resposta")
    )

    texto = f"{atrasada}📚 *{materia}* — {topico}\n\n{conteudo}"
    
    # Detecta tipo de questão e opções interativas
    q_info = detect_question_type(conteudo=conteudo, gabarito=item.get("gabarito"))
    tipo_questao = q_info["tipo"]

    # Armazena detalhes da resposta e metadados no context para revelar e avaliar no clique
    context.chat_data[f"rev_{revisao_id}"] = detalhes
    context.chat_data[f"rev_item_{revisao_id}"] = item_id
    context.chat_data[f"rev_meta_{revisao_id}"] = {
        "item_id": item_id,
        "revisao_id": revisao_id,
        "materia": materia,
        "topico": topico,
        "conteudo": conteudo,
        "gabarito": item.get("gabarito"),
        "detalhes": detalhes,
        "tipo": tipo_questao,
    }

    # Busca itens relacionados (teoria, sacada, pegadinha)
    relacionados = await fetch_related_items(item_id)
    context.chat_data[f"rel_{item_id}"] = relacionados

    keyboard = []
    if tipo_questao == "ce":
        keyboard.append([
            InlineKeyboardButton("🟢 Certo", callback_data=f"opt_{revisao_id}_C"),
            InlineKeyboardButton("🔴 Errado", callback_data=f"opt_{revisao_id}_E"),
        ])
        keyboard.append([
            InlineKeyboardButton("👁️ Ver Gabarito Direto", callback_data=f"ans_{revisao_id}")
        ])
    elif tipo_questao == "multipla":
        row_opts = [
            InlineKeyboardButton(f"({opt[0]})", callback_data=f"opt_{revisao_id}_{opt[1]}")
            for opt in q_info["opcoes"]
        ]
        keyboard.append(row_opts)
        keyboard.append([
            InlineKeyboardButton("👁️ Ver Gabarito Direto", callback_data=f"ans_{revisao_id}")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("Mostrar Resposta 👁️", callback_data=f"ans_{revisao_id}")
        ])

    concept_row = _build_concept_buttons(item_id, relacionados)
    if concept_row:
        keyboard.append(concept_row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_send_message(
        bot=context.bot,
        chat_id=chat_id,
        text=texto,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def handle_revisar_comando(update: Update, context: ContextTypes.DEFAULT_TYPE, categoria: str, msg: str):
    """Handler genérico para comandos de revisão."""
    if not await check_auth(update):
        return
    context.user_data["categoria_ativa"] = categoria or "questao"
    await safe_send_message(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        text=msg,
        parse_mode="Markdown"
    )
    await send_revision(update, context)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o comando /start e /revisar (foca exclusivamente em questões)."""
    await handle_revisar_comando(update, context, "questao", "👋 Olá! Buscando suas *questões* de revisão...")


async def enviar_teoria_solicitada(update: Update, context: ContextTypes.DEFAULT_TYPE, termo: str = ""):
    """Busca e apresenta material teórico solicitado explicitamente pelo usuário."""
    if not await check_auth(update):
        return

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            params = {"categoria": "teoria", "limit": 5, "only_parents": False}
            if termo:
                params["search"] = termo
            resp = await client.get(f"{API_BASE_URL}/items", params=params)
            resp.raise_for_status()
            teorias = resp.json()
        except Exception as e:
            logger.error(f"Erro ao buscar teoria: {e}")
            await safe_send_message(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                text="❌ Erro ao consultar a base de teorias."
            )
            return

    if not teorias:
        msg = "📚 Não encontrei nenhuma teoria cadastrada"
        if termo:
            msg += f" para o assunto *'{termo}'*."
        else:
            msg += " no momento."
        msg += "\n\n💡 Dica: Use `/teoria [termo]` (ex: `/teoria Crase` ou `/teoria agentes`)."
        await safe_send_message(
            bot=context.bot,
            chat_id=update.effective_chat.id,
            text=msg,
            parse_mode="Markdown"
        )
        return

    item = teorias[0]
    materia = item.get("materia_nome") or "Geral"
    topico = item.get("topico", "Geral")
    conteudo = item.get("conteudo", "")
    detalhes = item.get("detalhes_resposta")

    texto = f"📖 *TEORIA — {materia}*\n📌 *{topico}*\n\n{conteudo}"
    if detalhes:
        texto += f"\n\n💡 *Pontos-chave / Detalhes:*\n{detalhes}"

    keyboard = [
        [InlineKeyboardButton(f"🎯 Praticar Questões de {topico}", callback_data=f"practice_topic_{topico}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_send_message(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        text=texto,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def teoria_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o comando /teoria [assunto opcional]."""
    termo = " ".join(context.args).strip() if context.args else ""
    await enviar_teoria_solicitada(update, context, termo)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata os cliques nos botões inline."""
    if not await check_auth(update):
        return

    query = update.callback_query
    data = query.data

    # 1. Clique em Teoria, Sacada ou Pegadinha Relacionadas
    if data.startswith("rel_"):
        await query.answer()
        parts = data.split("_", 2)
        tipo = parts[1]
        target_item_id = parts[2]

        rel_data = context.chat_data.get(f"rel_{target_item_id}")
        if not rel_data:
            rel_data = await fetch_related_items(int(target_item_id))
            context.chat_data[f"rel_{target_item_id}"] = rel_data

        conceito = rel_data.get(tipo) if rel_data else None
        if not conceito:
            await query.answer(f"Nenhum registro de {tipo} vinculado encontrado.", show_alert=True)
            return

        icones = {"teoria": "📖", "sacada": "💡", "pegadinha": "⚠️"}
        titulos = {"teoria": "Teoria Relacionada", "sacada": "Sacada / Macete", "pegadinha": "Pegadinha de Prova"}

        icone = icones.get(tipo, "📌")
        titulo = titulos.get(tipo, tipo.capitalize())
        topico_c = conceito.get("topico") or "Tópico"
        conteudo_c = conceito.get("conteudo") or ""
        detalhes_c = conceito.get("detalhes_resposta")

        msg_conceito = f"{icone} *{titulo}*\n📚 *{topico_c}*\n\n{conteudo_c}"
        if detalhes_c:
            msg_conceito += f"\n\n🔍 *Observações pedagógicas:*\n{detalhes_c}"

        # Envia como nova mensagem sem apagar a questão!
        await safe_send_message(
            bot=context.bot,
            chat_id=query.message.chat_id,
            text=msg_conceito,
            parse_mode="Markdown"
        )
        return

    # 2. Praticar questões de um tópico vindo da leitura de teoria
    elif data.startswith("practice_topic_"):
        await query.answer("Iniciando revisão de questões...")
        context.user_data["categoria_ativa"] = "questao"
        await send_revision(update, context)
        return

    # 3. Avançar para a próxima questão
    elif data == "next_rev":
        await query.answer("Buscando próxima questão...")
        await send_revision(update, context)
        return

    # 4. Responder interativamente escolhendo a opção (C/E ou Alternativa)
    elif data.startswith("opt_"):
        await query.answer()
        parts = data.split("_", 2)
        revisao_id = parts[1]
        escolha = parts[2]

        meta = context.chat_data.get(f"rev_meta_{revisao_id}")
        item_id = context.chat_data.get(f"rev_item_{revisao_id}")
        detalhes = context.chat_data.get(f"rev_{revisao_id}")

        # Fallback de segurança se os metadados em memória tiverem expirado
        if not meta or not meta.get("gabarito"):
            gabarito_fallback = ""
            target_id = (meta.get("item_id") if meta else None) or item_id
            if target_id:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    try:
                        resp = await client.get(f"{API_BASE_URL}/items/{target_id}")
                        if resp.status_code == 200:
                            item_data = resp.json()
                            gabarito_fallback = item_data.get("gabarito") or ""
                            if not detalhes:
                                detalhes = item_data.get("detalhes_resposta") or ""
                    except Exception as e:
                        logger.warning(f"Falha ao buscar fallback de metadados para item #{target_id}: {e}")
            q_det = detect_question_type("", gabarito_fallback)
            meta = {
                "tipo": q_det["tipo"],
                "gabarito": gabarito_fallback,
                "detalhes": detalhes or "Gabarito não detalhado.",
                "item_id": target_id
            }

        gabarito_raw = meta.get("gabarito")
        tipo_q = meta.get("tipo", "ce")
        acertou, escolha_lbl, gab_lbl = evaluate_answer(escolha, gabarito_raw, tipo_q)

        # Regra do ciclo espaçado: 2 para acerto, 0 para erro
        nota = 2 if acertou else 0
        observacao = f"Marcou [{escolha_lbl}], gabarito [{gab_lbl}]"

        api_msg = "Registrado."
        ciclo_concluido = False
        proximo_intervalo = None

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                payload = {
                    "feita": True,
                    "revisao_id": int(revisao_id),
                    "desempenho": int(nota),
                    "observacao": observacao
                }
                resp = await client.post(f"{API_BASE_URL}/revisoes/responder", json=payload)
                resp.raise_for_status()
                resultado = resp.json()
                api_msg = resultado.get("mensagem", "Registrado.")
                ciclo_concluido = resultado.get("ciclo_concluido", False)
                proximo_intervalo = resultado.get("intervalo_dias")
            except Exception as e:
                logger.error(f"Erro ao registrar resposta interativa #{revisao_id}: {e}")
                api_msg = "Erro ao registrar no banco."

        if acertou:
            status_badge = "🎉 *Você ACERTOU!* ✅"
            if ciclo_concluido:
                ciclo_info = "🏆 *Ciclo concluído! Este conteúdo está consolidado na memória.*"
            elif proximo_intervalo:
                ciclo_info = f"📈 *Etapa avançada na escada: próxima revisão em +{proximo_intervalo} dias.*"
            else:
                ciclo_info = f"📈 *{api_msg}*"
        else:
            status_badge = "❌ *Você ERROU!*"
            ciclo_info = "🔄 *Etapa reiniciada: esta questão retornará amanhã (+1 dia) para fixação.*"

        detalhes_finais = meta.get("detalhes") or detalhes or "Sem detalhes adicionais."
        texto_feedback = (
            f"{query.message.text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_badge}\n"
            f"• *Sua resposta:* {escolha_lbl}\n"
            f"• *Gabarito oficial:* {gab_lbl}\n\n"
            f"💡 *Fundamentação / Detalhes:*\n{detalhes_finais}\n\n"
            f"_{ciclo_info}_"
        )

        keyboard = []
        target_item_id = meta.get("item_id") or item_id
        if target_item_id:
            rel_data = context.chat_data.get(f"rel_{target_item_id}")
            concept_row = _build_concept_buttons(target_item_id, rel_data)
            if concept_row:
                keyboard.append(concept_row)

        action_row = [
            InlineKeyboardButton("➡️ Próxima Questão", callback_data="next_rev")
        ]
        if acertou and not ciclo_concluido:
            action_row.append(
                InlineKeyboardButton("⚠️ Acertei no Chute (Difícil)", callback_data=f"rate_{revisao_id}_1")
            )
        keyboard.append(action_row)

        reply_markup = InlineKeyboardMarkup(keyboard)

        await safe_edit_message_text(
            query=query,
            text=texto_feedback,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    # 5. Mostrar Resposta da Questão (Fluxo Direto/Conceitual)
    elif data.startswith("ans_"):
        await query.answer()
        revisao_id = data.split("_")[1]
        detalhes = context.chat_data.get(f"rev_{revisao_id}", "Detalhes perdidos da memória local.")
        target_item_id = context.chat_data.get(f"rev_item_{revisao_id}")
        
        texto_resposta = f"💡 *Gabarito/Detalhes:*\n{detalhes}\n\n*Como foi seu desempenho?*"
        
        keyboard = []
        # Mantém botões de apoio conceitual acessíveis mesmo após a resposta
        if target_item_id:
            rel_data = context.chat_data.get(f"rel_{target_item_id}")
            concept_row = _build_concept_buttons(target_item_id, rel_data)
            if concept_row:
                keyboard.append(concept_row)

        keyboard.extend([
            [
                InlineKeyboardButton("❌ Errei (0)", callback_data=f"rate_{revisao_id}_0"),
                InlineKeyboardButton("⚠️ Difícil (1)", callback_data=f"rate_{revisao_id}_1")
            ],
            [
                InlineKeyboardButton("✅ Acertei (2)", callback_data=f"rate_{revisao_id}_2"),
                InlineKeyboardButton("⭐ Fácil (3)", callback_data=f"rate_{revisao_id}_3")
            ]
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await safe_edit_message_text(
            query=query,
            text=f"{query.message.text}\n\n{texto_resposta}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    # 4. Avaliar Desempenho (Errei/Acertei)
    elif data.startswith("rate_"):
        await query.answer()
        _, revisao_id, nota = data.split("_")
        
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
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
                logger.error(f"Erro ao registrar resposta da revisão #{revisao_id}: {e}")
                msg = "Erro ao se comunicar com a API."

        # Remove botões e finaliza o registro visual
        await safe_edit_message_text(
            query=query,
            text=f"{query.message.text}\n\n_Registro: {msg}_",
            reply_markup=None,
            parse_mode="Markdown"
        )
        
        # Envia a próxima questão garantindo captura de erros
        try:
            await send_revision(update, context)
        except Exception as e:
            logger.error(f"Erro ao enviar próxima questão após avaliação: {e}")


async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """Envia o resumo matinal às 08:00 focado estritamente em questões."""
    if not ADMIN_CHAT_ID or ADMIN_CHAT_ID == "seu_chat_id_opcional_aqui":
        logger.warning("Não há ADMIN_CHAT_ID configurado para o resumo diário.")
        return

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/revisoes/stats?categoria=questao")
            resp.raise_for_status()
            stats = resp.json()
        except Exception as e:
            logger.error(f"Erro ao buscar stats matinal: {e}")
            return

    atrasadas = stats.get("atrasadas", 0)
    devidas = stats.get("devidas_hoje", 0) - atrasadas
    total_hoje = atrasadas + devidas

    if total_hoje == 0:
        texto = "🌅 *Bom dia!* O seu cronograma de questões está vazio para hoje. Descanse ou estude tópicos novos!"
    else:
        texto = f"🌅 *Bom dia!* Aqui está o seu cronograma de revisões de hoje:\n\n"
        texto += f"🔴 *Atrasadas:* {atrasadas}\n"
        texto += f"🟡 *Para hoje:* {devidas}\n"
        texto += f"📊 *Total:* {total_hoje} questões pendentes.\n\n"
        texto += "Vamos bater essa meta?"

    keyboard = [[InlineKeyboardButton("🚀 Iniciar Revisões", callback_data="start_review_questoes")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_send_message(
        bot=context.bot,
        chat_id=ADMIN_CHAT_ID,
        text=texto,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def send_evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Envia cobrança noturna às 20:00 caso haja questões pendentes."""
    if not ADMIN_CHAT_ID or ADMIN_CHAT_ID == "seu_chat_id_opcional_aqui":
        return

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/revisoes/stats?categoria=questao")
            resp.raise_for_status()
            stats = resp.json()
        except Exception:
            return

    pendentes = stats.get("devidas_hoje", 0)
    if pendentes > 0:
        texto = f"⚠️ *Cobrança Hermes:* Você ainda tem {pendentes} questões pendentes para o dia de hoje!\n\nNão deixe a curva de esquecimento te pegar!"
        keyboard = [[InlineKeyboardButton("🚀 Começar agora", callback_data="start_review_questoes")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await safe_send_message(
            bot=context.bot,
            chat_id=ADMIN_CHAT_ID,
            text=texto,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata mensagens de texto do usuário (ex.: pedidos de teoria em linguagem natural)."""
    if not await check_auth(update):
        return

    texto_msg = update.effective_message.text.strip()
    texto_lower = texto_msg.lower()

    if "teoria" in texto_lower:
        # Extrai o termo de busca após a palavra 'teoria'
        partes = texto_lower.split("teoria", 1)
        termo = partes[1].strip() if len(partes) > 1 else ""
        termo = termo.lstrip("de").lstrip("do").lstrip("da").strip()
        await enviar_teoria_solicitada(update, context, termo)
        return

    # Mensagem de ajuda padrão
    msg_ajuda = (
        "🤖 *Comandos disponíveis no Hermes:*\n\n"
        "🎯 `/revisar` — Iniciar sessão de questões pendentes\n"
        "📖 `/teoria [assunto]` — Consultar resumo teórico sob demanda (ex: `/teoria Crase`)\n"
        "📊 As revisões diárias trazem apenas questões com gabarito e botões para consultar a teoria quando você quiser!"
    )
    await safe_send_message(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        text=msg_ajuda,
        parse_mode="Markdown"
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registra exceções não tratadas nos handlers do Telegram."""
    logger.error("Exceção não tratada capturada pelo handler do Telegram:", exc_info=context.error)


def main():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "seu_token_aqui":
        logger.error("A variável TELEGRAM_BOT_TOKEN não está configurada corretamente no .env!")
        sys.exit(1)
    logger.info("Iniciando o Hermes Telegram Bot...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("revisar", start_command))
    application.add_handler(CommandHandler("teoria", teoria_command))
    application.add_handler(CommandHandler("revisar_teoria", teoria_command))

    # Tratamento para o botão de Iniciar do Cronograma
    async def inline_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        context.user_data["categoria_ativa"] = "questao"
        await send_revision(update, context)

    application.add_handler(CallbackQueryHandler(inline_start_handler, pattern="^start_review_questoes$"))
    application.add_handler(CallbackQueryHandler(inline_start_handler, pattern="^start_review_todas$"))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Handler para mensagens de texto comuns
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Registra handler global de erros
    application.add_error_handler(error_handler)

    # Configurar Cron Jobs
    tz = datetime.timezone(datetime.timedelta(hours=-3))  # Horário de Brasília
    job_queue = application.job_queue
    
    hora_manha = datetime.time(hour=8, minute=0, tzinfo=tz)
    hora_noite = datetime.time(hour=20, minute=0, tzinfo=tz)
    
    job_queue.run_daily(send_daily_summary, time=hora_manha)
    job_queue.run_daily(send_evening_reminder, time=hora_noite)

    logger.info("Cron Jobs configurados para 08:00 e 20:00 (GMT-3).")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
