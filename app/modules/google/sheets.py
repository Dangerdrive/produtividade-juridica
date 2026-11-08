"""Cliente genérico da Google Sheets API v4.

Este módulo pertence à camada de integração Google e não conhece nenhum
domínio específico. Cada módulo (prazos, repasses, ...) instancia o cliente com
a sua própria planilha/aba.

Autenticação: conta de serviço (``GOOGLE_SERVICE_ACCOUNT_JSON``). Compartilhe a
planilha com o e-mail da conta de serviço como Editor.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.modules.google.auth import GoogleAuth, GoogleAuthError

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
ESCOPO_PLANILHAS = "https://www.googleapis.com/auth/spreadsheets"


class SheetsError(Exception):
    """Falha na comunicação com a API do Google Sheets."""


def letra_coluna(numero: int) -> str:
    """Converte um índice 1-based em letra de coluna (1 -> A, 27 -> AA)."""
    if numero < 1:
        raise ValueError("O índice da coluna deve ser >= 1.")

    letras: list[str] = []
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras.append(chr(ord("A") + resto))
    return "".join(reversed(letras))


class SheetsClient:
    """Cliente mínimo e genérico da Sheets API v4."""

    def __init__(
        self,
        settings: Settings,
        *,
        sheet_id: str | None = None,
        aba: str | None = None,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        auth: GoogleAuth | None = None,
    ) -> None:
        self.settings = settings
        self.sheet_id = sheet_id or settings.google_sheet_id
        self.aba = aba or settings.google_sheet_tab
        self._client = client or httpx.Client(timeout=30.0)
        self._sleep = sleep
        self._auth = auth or GoogleAuth(settings, client=self._client, sleep=sleep)

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------
    def _access_token(self) -> str:
        try:
            return self._auth.access_token(ESCOPO_PLANILHAS)
        except GoogleAuthError as exc:
            raise SheetsError(str(exc)) from exc

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _url(self, sufixo: str = "") -> str:
        if not self.sheet_id:
            raise SheetsError("Planilha não configurada (ID vazio).")
        return f"{SHEETS_API}/{self.sheet_id}{sufixo}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _enviar(self, metodo: str, sufixo: str, **kwargs) -> dict:
        try:
            resposta = self._client.request(
                metodo, self._url(sufixo), headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise SheetsError(f"Falha de rede ao acessar o Sheets: {exc}") from exc

        if resposta.status_code >= 400:
            raise SheetsError(
                f"Sheets HTTP {resposta.status_code}: {resposta.text[:300]}"
            )
        return resposta.json() if resposta.content else {}

    # ------------------------------------------------------------------
    # Operações
    # ------------------------------------------------------------------
    def _range(self, referencia: str, aba: str | None = None) -> str:
        return f"'{aba or self.aba}'!{referencia}"

    def ler_valores(
        self, referencia: str = "A1:ZZ", *, aba: str | None = None
    ) -> list[list[str]]:
        """Lê um intervalo. Valores vazios nas pontas são omitidos pela API."""
        alvo = quote(self._range(referencia, aba), safe="")
        dados = self._enviar("GET", f"/values/{alvo}")
        return dados.get("values", [])

    def escrever_valores(
        self, referencia: str, valores: list[list[str]], *, aba: str | None = None
    ) -> dict:
        """Sobrescreve um intervalo."""
        alvo = quote(self._range(referencia, aba), safe="")
        return self._enviar(
            "PUT",
            f"/values/{alvo}",
            params={"valueInputOption": "USER_ENTERED"},
            json={"values": valores},
        )

    def anexar(
        self,
        valores: list[list[str]],
        *,
        referencia: str = "A1",
        aba: str | None = None,
    ) -> str:
        """Anexa linhas ao final da tabela. Devolve o intervalo atualizado."""
        alvo = quote(self._range(referencia, aba), safe="")
        dados = self._enviar(
            "POST",
            f"/values/{alvo}:append",
            params={
                "valueInputOption": "USER_ENTERED",
                "insertDataOption": "INSERT_ROWS",
            },
            json={"values": valores},
        )
        return (dados.get("updates") or {}).get("updatedRange", "")

    def atualizar_linhas(
        self,
        linhas: dict[int, list[str]],
        *,
        ultima_coluna: str = "ZZ",
        aba: str | None = None,
    ) -> None:
        """Atualiza várias linhas de uma vez (``{linha: valores}``)."""
        if not linhas:
            return

        dados = [
            {
                "range": self._range(f"A{n}:{ultima_coluna}{n}", aba),
                "values": [valores],
            }
            for n, valores in sorted(linhas.items())
        ]

        self._enviar(
            "POST",
            "/values:batchUpdate",
            json={"valueInputOption": "USER_ENTERED", "data": dados},
        )

    def limpar(self, referencia: str, *, aba: str | None = None) -> dict:
        """Limpa os valores de um intervalo."""
        alvo = quote(self._range(referencia, aba), safe="")
        return self._enviar("POST", f"/values/{alvo}:clear", json={})

    def garantir_cabecalho(
        self,
        cabecalhos: Sequence[str],
        *,
        ultima_coluna: str | None = None,
        aba: str | None = None,
    ) -> bool:
        """Cria/corrige a linha 1. Devolve ``True`` se precisou escrever."""
        esperado = [str(c).strip() for c in cabecalhos]
        if not esperado:
            return False

        ultima = ultima_coluna or letra_coluna(len(esperado))

        try:
            atual = (self.ler_valores(f"A1:{ultima}1", aba=aba) or [[]])[0]
        except SheetsError:
            atual = []

        if [str(c).strip() for c in atual] == esperado:
            return False

        self.escrever_valores(f"A1:{ultima}1", [esperado], aba=aba)
        return True

    def mapa_ids(
        self,
        *,
        coluna: str = "A",
        inicio: int = 2,
        aba: str | None = None,
    ) -> dict[str, int]:
        """``{valor_da_coluna: número_da_linha}``."""
        valores = self.ler_valores(f"{coluna}{inicio}:{coluna}", aba=aba)
        mapa: dict[str, int] = {}

        for indice, linha in enumerate(valores, start=inicio):
            if not linha:
                continue
            chave = str(linha[0]).strip()
            if chave and chave not in mapa:
                mapa[chave] = indice

        return mapa

    # ------------------------------------------------------------------
    # Planilha
    # ------------------------------------------------------------------
    def obter_metadados(self) -> dict:
        """Metadados da planilha (abas, propriedades)."""
        return self._enviar("GET", "")

    def criar_planilha(self, titulo: str) -> dict:
        """Cria uma nova planilha e devolve os metadados."""
        try:
            resposta = self._client.post(
                SHEETS_API,
                headers=self._headers(),
                json={"properties": {"title": titulo}},
            )
        except httpx.HTTPError as exc:
            raise SheetsError(f"Falha de rede ao criar planilha: {exc}") from exc

        if resposta.status_code >= 400:
            raise SheetsError(
                f"Sheets HTTP {resposta.status_code}: {resposta.text[:300]}"
            )
        return resposta.json()
