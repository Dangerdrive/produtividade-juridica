"""Autenticação de conta de serviço do Google.

Centraliza a leitura das credenciais e a troca do JWT (RS256) por um access
token, com cache por escopo. É usada pelos clientes de Sheets e Docs.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import jwt

from app.core.config import Settings

TOKEN_PADRAO = "https://oauth2.googleapis.com/token"


class GoogleAuthError(Exception):
    """Falha ao carregar credenciais ou obter um access token."""


class GoogleAuth:
    """Obtém access tokens para a conta de serviço configurada."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.Client(timeout=30.0)
        self._sleep = sleep
        self._tokens: dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------
    def credenciais(self) -> dict:
        """Lê o JSON da conta de serviço (variável ou arquivo)."""
        bruto = (self.settings.google_service_account_json or "").strip()
        arquivo = (self.settings.google_service_account_file or "").strip()

        if not bruto and arquivo:
            caminho = Path(arquivo).expanduser()
            try:
                bruto = caminho.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise GoogleAuthError(
                    f"Não foi possível ler GOOGLE_SERVICE_ACCOUNT_FILE ({caminho}): {exc}"
                ) from exc

        if not bruto:
            raise GoogleAuthError(
                "GOOGLE_SERVICE_ACCOUNT_JSON ou GOOGLE_SERVICE_ACCOUNT_FILE "
                "não configurado."
            )

        if not bruto.startswith("{"):
            try:
                bruto = base64.b64decode(bruto).decode("utf-8")
            except Exception as exc:  # noqa: BLE001
                raise GoogleAuthError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON não é JSON nem base64 válido."
                ) from exc

        try:
            info = json.loads(bruto)
        except json.JSONDecodeError as exc:
            raise GoogleAuthError("GOOGLE_SERVICE_ACCOUNT_JSON inválido.") from exc

        for chave in ("client_email", "private_key"):
            if not info.get(chave):
                raise GoogleAuthError(f"Credencial sem '{chave}'.")
        return info

    def access_token(self, escopo: str) -> str:
        """Devolve um access token válido para o escopo, com cache em memória."""
        agora = time.monotonic()
        token, expira_em = self._tokens.get(escopo, ("", 0.0))
        if token and agora < expira_em:
            return token

        info = self.credenciais()
        token_uri = info.get("token_uri", TOKEN_PADRAO)
        emitido_em = int(time.time())

        claims = {
            "iss": info["client_email"],
            "scope": escopo,
            "aud": token_uri,
            "iat": emitido_em,
            "exp": emitido_em + 3600,
        }

        try:
            assertion = jwt.encode(claims, info["private_key"], algorithm="RS256")
        except Exception as exc:  # noqa: BLE001
            raise GoogleAuthError(
                f"Falha ao assinar o JWT da conta de serviço: {exc}"
            ) from exc

        try:
            resposta = self._client.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        except httpx.HTTPError as exc:
            raise GoogleAuthError(f"Falha de rede ao obter token do Google: {exc}") from exc

        if resposta.status_code != 200:
            raise GoogleAuthError(
                f"Falha ao obter token do Google (HTTP {resposta.status_code}): "
                f"{resposta.text[:200]}"
            )

        dados = resposta.json()
        novo_token = dados.get("access_token")
        if not novo_token:
            raise GoogleAuthError("Resposta de token sem 'access_token'.")

        # Renova 60 s antes do vencimento.
        self._tokens[escopo] = (
            novo_token,
            agora + max(int(dados.get("expires_in", 3600)) - 60, 30),
        )
        return novo_token
