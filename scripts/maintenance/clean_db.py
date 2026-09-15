import logging
import psycopg2
from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clean_db")


def reset_database():
    """Limpa todas as tabelas e reinicia os identificadores (SERIAL) no PostgreSQL."""
    conn = psycopg2.connect(**settings.db_params)
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE itens_estudo, videos_processados RESTART IDENTITY CASCADE;")
            conn.commit()
            logger.info("Banco de dados limpo com sucesso! Sequências reiniciadas do ID 1.")
    finally:
        conn.close()


if __name__ == "__main__":
    reset_database()
