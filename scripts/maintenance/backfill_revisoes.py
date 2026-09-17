#!/usr/bin/env python3
"""
Backfill do ciclo de revisão espaçada (Curva do Esquecimento - Ebbinghaus).

Cria a primeira revisão para todo item de estudo que ainda não possui nenhuma.
Uso:
    docker exec -i study-rag-api python < scripts/backfill_revisoes.py
    docker exec -i study-rag-api python < scripts/backfill_revisoes.py --categoria questao
    docker exec -i study-rag-api python < scripts/backfill_revisoes.py --base-hoje
"""
import argparse
import json
import os
import sys

# Garante raiz do projeto no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src import db
from src import revisoes


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill das revisões espaçadas.")
    parser.add_argument("--categoria", default=None,
                        help="Gerar apenas para uma categoria (teoria|sacada|pegadinha|questao)")
    parser.add_argument("--base-hoje", action="store_true",
                        help="Agenda a partir de hoje em vez da data do próprio item")
    args = parser.parse_args()

    db.init_db()

    categorias = [args.categoria] if args.categoria else None
    base = revisoes.agora_local() if args.base_hoje else None

    resultado = revisoes.backfill_revisoes(categorias=categorias, base_date=base)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))

    stats = revisoes.estatisticas()
    print("\n--- Estado do ciclo ---")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
