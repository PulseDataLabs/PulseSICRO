#!/usr/bin/env python
# coding: utf-8
"""
PulseSICRO – Orquestrador de scrapers
"""

import argparse
import importlib
import json
import logging
import sys
import traceback
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Setup standard logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_all")

def _banner() -> None:
    now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    print()
    print("═" * 80)
    print("  🧱 PulseSICRO  -  Pipeline de Custos de Infraestrutura (DNIT)")
    print(f"  {now}")
    print("═" * 80)
    print()

def _section(title: str) -> None:
    print()
    print("─" * 80)
    print(f"  ▶  {title}")
    print("─" * 80)

def discover_scrapers() -> dict[str, dict]:
    """Varre scrapers/ e retorna metadados de cada classe scraper."""
    scrapers: dict[str, dict] = {}
    scrapers_dir = Path(__file__).resolve().parent / "scrapers"

    for file_path in sorted(scrapers_dir.glob("*.py")):
        module_name = file_path.stem
        if module_name in ("__init__", "generic_scraper"):
            continue
        try:
            # Garante que scrapers/ está no path
            sys.path.insert(0, str(file_path.parent.parent))
            mod = importlib.import_module(f"scrapers.{module_name}")
            class_name = "".join(w.capitalize() for w in module_name.split("_")) + "Scraper"
            if hasattr(mod, class_name):
                cls = getattr(mod, class_name)
                scrapers[module_name] = {
                    "group": getattr(cls, "group", "sicro"),
                    "enabled": getattr(cls, "enabled", True),
                    "phase": getattr(cls, "phase", 1),
                    "class_name": class_name,
                    "title": getattr(cls, "title", module_name.replace("_", " ").title()),
                }
        except Exception as e:
            logger.warning(f"  ⚠  Não foi possível carregar metadados de {module_name}: {e}")

    return scrapers

def run_scraper(module_name: str) -> tuple[bool, float, Optional[str]]:
    t0 = time.time()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        mod = importlib.import_module(f"scrapers.{module_name}")
        class_name = "".join(w.capitalize() for w in module_name.split("_")) + "Scraper"
        if hasattr(mod, class_name):
            getattr(mod, class_name)().run()
        else:
            return False, time.time() - t0, f"Módulo {module_name} não possui classe {class_name}."
        return True, time.time() - t0, None
    except Exception:
        return False, time.time() - t0, traceback.format_exc()

def save_pipeline_status(
    results: dict[str, tuple[bool, float, Optional[str]]],
    total_elapsed: float,
) -> None:
    root_dir = Path(__file__).resolve().parent
    status_path = root_dir / "data" / "pipeline_status.json"
    scrapers_registry = discover_scrapers()
    active_scrapers = {k: v for k, v in scrapers_registry.items() if v["enabled"]}

    status_data: dict = {
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": total_elapsed,
        "status": "success",
        "summary": {"total": 0, "success": 0, "failed": 0},
        "scrapers": {},
    }

    if status_path.exists():
        try:
            with status_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded.get("scrapers"), dict):
                status_data["scrapers"] = loaded["scrapers"]
        except Exception as e:
            logger.warning(f"  ⚠  Não foi possível carregar status anterior: {e}")

    now_iso = datetime.now().isoformat()
    for name, (success, elapsed, err) in results.items():
        status_data["scrapers"][name] = {
            "status": "success" if success else "error",
            "elapsed_seconds": elapsed,
            "error": err,
            "timestamp": now_iso,
        }

    for name in active_scrapers:
        if name not in status_data["scrapers"]:
            status_data["scrapers"][name] = {
                "status": "unknown", "elapsed_seconds": 0.0,
                "error": None, "timestamp": None,
            }

    ok_cnt = sum(1 for s in status_data["scrapers"].values() if s["status"] == "success")
    fail_cnt = sum(1 for s in status_data["scrapers"].values() if s["status"] == "error")
    
    status_data["summary"] = {
        "total": len(active_scrapers),
        "success": ok_cnt,
        "failed": fail_cnt,
    }
    status_data["status"] = "error" if fail_cnt > 0 else "success"

    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        with status_path.open("w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        print("  📄 pipeline_status.json atualizado")
    except Exception as e:
        logger.error(f"  ✖  Erro ao salvar status do pipeline: {e}")

def _summary_table(
    results: dict[str, tuple[bool, float, Optional[str]]],
    registry: dict[str, dict],
    total_elapsed: float,
) -> None:
    ok = [(n, r) for n, r in results.items() if r[0]]
    fail = [(n, r) for n, r in results.items() if not r[0]]

    print()
    print("═" * 80)
    print("  RESUMO FINAL")
    print("─" * 80)
    print(f"  Total: {len(results)} scrapers  │  ✔ {len(ok)} ok  │  ✖ {len(fail)} erros  │  ⏱  {total_elapsed:.1f}s")
    print("═" * 80)
    print()

def main():
    parser = argparse.ArgumentParser(
        description="🧱 PulseSICRO – Orquestrador de scrapers de dados do SICRO (DNIT)",
    )
    parser.add_argument("--scraper", choices=sorted(discover_scrapers().keys()), metavar="SCRAPER", help="Executa um scraper específico")
    parser.add_argument("--list", action="store_true", help="Lista todos os scrapers disponíveis e sai")

    args = parser.parse_args()
    registry = discover_scrapers()
    
    if args.list:
        _banner()
        print("  Scrapers disponíveis:")
        for name, info in sorted(registry.items()):
            marker = "●" if info["enabled"] else "○"
            print(f"    {marker}  {name:<30} {info.get('title', '')}")
        print()
        return

    _banner()
    t0 = time.time()
    
    if args.scraper:
        targets = {args.scraper: registry[args.scraper]}
    else:
        targets = {n: info for n, info in registry.items() if info["enabled"]}

    if not targets:
        print("  ✖  Nenhum scraper ativo encontrado.")
        sys.exit(1)

    results = {}
    
    if args.scraper:
        name = args.scraper
        _section(f"Executando scraper individual: {name}")
        success, elapsed, err = run_scraper(name)
        if success:
            print(f"  ✔  {name} concluído com sucesso ({elapsed:.1f}s)")
        else:
            print(f"  ✖  {name} falhou ({elapsed:.1f}s)")
        results[name] = (success, elapsed, err)
    else:
        phase1 = [n for n, i in targets.items() if i["phase"] == 1]
        phase2 = [n for n, i in targets.items() if i["phase"] == 2]
        
        if phase1:
            _section("Fase 1 - Dados Primários (Insumos e Equipamentos)")
            for name in phase1:
                print(f"  ⚙  Iniciando {name}...")
                success, elapsed, err = run_scraper(name)
                if success:
                    print(f"  ✔  {name} concluído com sucesso ({elapsed:.1f}s)")
                else:
                    print(f"  ✖  {name} falhou ({elapsed:.1f}s)")
                results[name] = (success, elapsed, err)
                
        if phase2:
            _section("Fase 2 - Dados Compostos (Composições)")
            for name in phase2:
                print(f"  ⚙  Iniciando {name}...")
                success, elapsed, err = run_scraper(name)
                if success:
                    print(f"  ✔  {name} concluído com sucesso ({elapsed:.1f}s)")
                else:
                    print(f"  ✖  {name} falhou ({elapsed:.1f}s)")
                results[name] = (success, elapsed, err)

    total_elapsed = time.time() - t0
    
    _section("Pós-processamento")
    save_pipeline_status(results, total_elapsed)
    _summary_table(results, registry, total_elapsed)
    
    if any(not r[0] for r in results.values()):
        sys.exit(1)

if __name__ == "__main__":
    main()
