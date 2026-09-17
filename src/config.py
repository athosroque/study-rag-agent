import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LiteLLM
    LITELLM_BASE_URL: str = "http://litellm:4000/v1"
    LITELLM_API_KEY: str = "sk-master-5184388f4d1f9ae9299217ce84dccaf0d3bf2c78c5aa0a2db831ad308e82a721"
    DEFAULT_LLM_MODEL: str = "glm-5.3-flash"
    MODEL_FAST: str = "glm-5.3-flash"
    MODEL_MID: str = "glm-5.3-flash"
    MODEL_STRONG: str = "strong"

    # PostgreSQL + pgvector
    POSTGRES_HOST: str = "study-rag-db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "study_rag"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"

    # Langfuse Observability
    LANGFUSE_PUBLIC_KEY: Optional[str] = "pk-lf-944c3458-c3e3-40fb-b7c0-e15844a2c848"
    LANGFUSE_SECRET_KEY: Optional[str] = "sk-lf-2d440ba0-ab8f-464f-b739-3994753b057b"
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # LangSmith Observability
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: Optional[str] = None
    LANGCHAIN_PROJECT: str = "study-rag-agent"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"

    # Embedding Configuration
    EMBEDDING_PROVIDER: str = "fastembed"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    MIN_SIMILARITY_THRESHOLD: float = 0.50

    # Revisão Espaçada (Curva do Esquecimento - Ebbinghaus)
    REVISAO_INTERVALOS_DIAS: str = "1,7,15,30"
    REVISAO_HORA_AGENDAMENTO: int = 8
    REVISAO_TZ_OFFSET_HORAS: int = -3

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def db_params(self) -> dict:
        return {
            "dbname": self.POSTGRES_DB,
            "user": self.POSTGRES_USER,
            "password": self.POSTGRES_PASSWORD,
            "host": self.POSTGRES_HOST,
            "port": self.POSTGRES_PORT,
        }

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()

# Sincroniza variáveis com os.environ para que o LangChain / LangGraph ative o tracing automaticamente
if settings.LANGCHAIN_TRACING_V2 and settings.LANGCHAIN_API_KEY:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
    os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGCHAIN_ENDPOINT

