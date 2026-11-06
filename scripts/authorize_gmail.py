#!/usr/bin/env python3
"""Gera o ``GMAIL_REFRESH_TOKEN`` (OAuth2) para uso em produção.

Uso:
    python scripts/authorize_gmail.py --client-id XXX --client-secret YYY

O script sobe um servidor local em ``http://localhost:8080/`` apenas para
capturar o ``code`` devolvido pelo Google. Nenhum dado sai da sua máquina
além da troca de token com ``oauth2.googleapis.com``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ESCOPO_PADRAO = "https://www.googleapis.com/auth/gmail.modify"

_codigo_recebido: dict[str, str] = {}
_evento = threading.Event()


def carregar_env(caminho: Path) -> dict[str, str]:
    """Leitor simples de .env (evita dependência extra só para isso)."""
    valores: dict[str, str] = {}
    if not caminho.exists():
        return valores

    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valores[chave.strip()] = valor.strip().strip('"').strip("'")
    return valores


class _CapturaCallback(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - API do http.server
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if "error" in params:
            _codigo_recebido["erro"] = params["error"][0]
            _evento.set()
            self._responder("Autorização negada. Pode fechar esta aba.")
            return

        if "code" in params:
            _codigo_recebido["code"] = params["code"][0]
            _evento.set()
            self._responder("Autorização concluída! Pode fechar esta aba e voltar ao terminal.")
            return

        self._responder("Aguardando o código de autorização...", status=404)

    def _responder(self, mensagem: str, *, status: int = 200) -> None:
        corpo = f"<html><body style='font:16px system-ui;padding:2rem'>{mensagem}</body></html>"
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(corpo.encode("utf-8"))

    def log_message(self, *args) -> None:  # silencia o log padrão
        return


def gerar_url(client_id: str, redirect_uri: str, escopo: str) -> str:
    parametros = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": escopo,
        "access_type": "offline",
        "prompt": "consent",
        "state": secrets.token_urlsafe(16),
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(parametros)}"


def trocar_codigo_por_tokens(
    *, client_id: str, client_secret: str, code: str, redirect_uri: str
) -> dict:
    resposta = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    resposta.raise_for_status()
    return resposta.json()


def escrever_env(caminho: Path, chave: str, valor: str) -> None:
    """Insere/atualiza uma chave no .env preservando o restante do arquivo."""
    linhas = caminho.read_text(encoding="utf-8").splitlines() if caminho.exists() else []
    linhas = [linha for linha in linhas if not linha.startswith(f"{chave}=")]
    linhas.append(f"{chave}={valor}")
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Autoriza o acesso ao Gmail (OAuth2)")
    parser.add_argument("--client-id", default=os.getenv("GMAIL_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.getenv("GMAIL_CLIENT_SECRET", ""))
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--escopo", default=ESCOPO_PADRAO)
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Grava o refresh token no .env depois de gerado",
    )
    args = parser.parse_args()

    env = carregar_env(Path(".env"))
    client_id = args.client_id or env.get("GMAIL_CLIENT_ID", "")
    client_secret = args.client_secret or env.get("GMAIL_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print(
            "Informe --client-id/--client-secret (ou defina GMAIL_CLIENT_ID e "
            "GMAIL_CLIENT_SECRET no ambiente/.env).",
            file=sys.stderr,
        )
        return 1

    redirect_uri = f"http://localhost:{args.port}/"
    url = gerar_url(client_id, redirect_uri, args.escopo)

    servidor = HTTPServer(("localhost", args.port), _CapturaCallback)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()

    print("\n1) Abra a URL abaixo, escolha a conta do Gmail e autorize:\n")
    print(f"   {url}\n")
    with contextlib.suppress(Exception):
        webbrowser.open(url)

    print("2) Aguardando o retorno do Google no navegador...")
    _evento.wait(timeout=300)
    servidor.shutdown()

    if "erro" in _codigo_recebido:
        print(f"\nAutorização recusada: {_codigo_recebido['erro']}", file=sys.stderr)
        return 1

    codigo = _codigo_recebido.get("code")
    if not codigo:
        print("\nTempo esgotado aguardando o código de autorização.", file=sys.stderr)
        return 1

    tokens = trocar_codigo_por_tokens(
        client_id=client_id,
        client_secret=client_secret,
        code=codigo,
        redirect_uri=redirect_uri,
    )

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(
            "\nO Google não devolveu refresh_token. Remova o acesso do app em "
            "https://myaccount.google.com/permissions e tente novamente.",
            file=sys.stderr,
        )
        return 1

    print("\n✔ Refresh token gerado com sucesso.\n")
    print(f"GMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={refresh_token}\n")

    if args.write_env:
        caminho_env = Path(".env")
        escrever_env(caminho_env, "GMAIL_CLIENT_ID", client_id)
        escrever_env(caminho_env, "GMAIL_CLIENT_SECRET", client_secret)
        escrever_env(caminho_env, "GMAIL_REFRESH_TOKEN", refresh_token)
        print(f"Valores gravados em {caminho_env.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
