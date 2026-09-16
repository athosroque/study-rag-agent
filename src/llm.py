import logging
from typing import Optional, List, Any
from langchain_openai import ChatOpenAI
from src.config import settings

logger = logging.getLogger(__name__)


_langfuse_client = None
_langsmith_client = None


def get_langsmith_client() -> Optional[Any]:
    """Retorna a instância singleton do cliente LangSmith se as credenciais existirem."""
    global _langsmith_client
    if _langsmith_client is not None:
        return _langsmith_client
    if settings.LANGCHAIN_TRACING_V2 and settings.LANGCHAIN_API_KEY:
        try:
            from langsmith import Client
            _langsmith_client = Client(
                api_key=settings.LANGCHAIN_API_KEY,
                api_url=settings.LANGCHAIN_ENDPOINT,
            )
            logger.info(f"Cliente LangSmith inicializado com sucesso (projeto: {settings.LANGCHAIN_PROJECT}).")
            return _langsmith_client
        except Exception as e:
            logger.warning(f"Não foi possível inicializar LangSmith client: {e}")
            return None
    return None


def get_langfuse_client() -> Optional[Any]:
    """Retorna a instância singleton do cliente Langfuse se as credenciais existirem."""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
    if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
        try:
            from langfuse import Langfuse
            _langfuse_client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_HOST,
            )
            logger.info("Cliente Langfuse singleton inicializado com sucesso.")
            return _langfuse_client
        except Exception as e:
            logger.warning(f"Não foi possível inicializar Langfuse client: {e}")
            return None
    return None


def get_langfuse_callback() -> Optional[Any]:
    """Retorna o callback handler do Langfuse se as credenciais estiverem disponíveis."""
    client = get_langfuse_client()
    if client:
        try:
            from langfuse.langchain import CallbackHandler
            handler = CallbackHandler(public_key=settings.LANGFUSE_PUBLIC_KEY)
            return handler
        except Exception as e:
            logger.warning(f"Não foi possível inicializar Langfuse Callback: {e}")
            return None
    return None


def flush_langfuse():
    """Descarrega imediatamente eventos e traces pendentes no Langfuse."""
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
            logger.info("Langfuse traces descarregados com sucesso (flush).")
        except Exception as e:
            logger.warning(f"Falha ao executar flush no Langfuse: {e}")


def get_llm(
    model: Optional[str] = None,
    tier: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    callbacks: Optional[List[Any]] = None
) -> ChatOpenAI:
    """
    Retorna uma instância de ChatOpenAI configurada para o LiteLLM.
    Suporta especificação direta do modelo (ex: 'glm-5.3-flash') ou via tier legado.
    """
    if model:
        model_name = model
    elif tier:
        model_map = {
            "fast": settings.MODEL_FAST,
            "mid": settings.MODEL_MID,
            "strong": settings.MODEL_STRONG,
        }
        model_name = model_map.get(tier.lower(), settings.DEFAULT_LLM_MODEL)
    else:
        model_name = settings.DEFAULT_LLM_MODEL

    all_callbacks = list(callbacks or [])
    lf_callback = get_langfuse_callback()
    if lf_callback and lf_callback not in all_callbacks:
        all_callbacks.append(lf_callback)

    logger.info(f"Instanciando LLM -> modelo '{model_name}' em {settings.LITELLM_BASE_URL}")

    return ChatOpenAI(
        model=model_name,
        openai_api_base=settings.LITELLM_BASE_URL,
        openai_api_key=settings.LITELLM_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
        callbacks=all_callbacks if all_callbacks else None,
        timeout=120,
    )
