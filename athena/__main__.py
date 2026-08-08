#!/usr/bin/env python3
"""Entry point para o Athena-MCP."""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="athena",
        description="Athena-MCP: Dashboard OmniRouter para CLIs de agentes de IA",
    )
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    # dashboard
    dashboard_parser = subparsers.add_parser("dashboard", help="Inicia o dashboard web")
    dashboard_parser.add_argument("--host", default="127.0.0.1", help="Host do dashboard")
    dashboard_parser.add_argument("--port", type=int, default=20128, help="Porta do dashboard")

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="Inicia o servidor MCP (stdio)")

    # test
    test_parser = subparsers.add_parser("test", help="Testa providers disponíveis")

    args = parser.parse_args()

    if args.command == "dashboard":
        from athena.dashboard.app import run_dashboard
        run_dashboard()
    elif args.command == "mcp":
        from athena.mcp_server import run_stdio_server
        run_stdio_server()
    elif args.command == "test":
        _run_test()
    else:
        parser.print_help()
        sys.exit(1)


def _run_test() -> None:
    from athena.providers import list_providers
    from athena.combos import ensure_default_combo
    from athena.router import test_combo

    print("=" * 50)
    print("ATHENA-MCP TESTE RÁPIDO")
    print("=" * 50)

    providers = list_providers()
    print(f"\nProviders detectados: {len([p for p in providers if p['available']])}/{len(providers)}")
    for p in providers:
        status = "✅" if p["available"] else "❌"
        print(f"  {status} {p['name']} ({p['id']})")

    combo = ensure_default_combo()
    print(f"\nCombo padrão: {combo.name}")
    print(f"Chain: {' → '.join(s.provider_id for s in combo.chain)}")

    print("\nTestando combo 'default'...")
    results = test_combo("default")
    for r in results:
        status = "✅" if r["status"] == "ok" else "❌"
        print(f"  {status} {r['provider_id']}: {r['status']}")

    print("\n" + "=" * 50)
    print("TESTE CONCLUÍDO!")


if __name__ == "__main__":
    main()
