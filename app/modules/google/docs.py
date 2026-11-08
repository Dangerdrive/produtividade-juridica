"""Cliente mínimo da Google Docs API v1.

Permite ler o texto de um documento, criar documentos, inserir texto e
substituir trechos. Usa a mesma conta de serviço do módulo Google.
"""

from __future__ import annotations

import time

import httpx

from app.core.config import Settings
from app.modules.google.auth import GoogleAuth, GoogleAuthError

DOCS_API = "https://docs.googleapis.com/v1/documents"
ESCOPO_DOCS = "https://www.googleapis.com/auth/documents"


class DocsError(Exception):
    """Falha na comunicação com a API do Google Docs."""


def extrair_texto(documento: dict) -> str:
    """Extrai o texto corrido de um documento retornado pela Docs API."""
    partes: list[str] = []

    for elemento in (documento.get("body") or {}).get("content") or []:
        paragrafo = elemento.get("paragraph") or {}
        for pedaco in paragrafo.get("elements") or []:
            texto = (pedaco.get("textRun") or {}).get("content")
            if texto:
                partes.append(texto)

        if elemento.get("table"):
            for linha in elemento["table"].get("tableRows") or []:
                for celula in linha.get("tableCells") or []:
                    for conteudo in celula.get("content") or []:
                        paragrafo_celula = conteudo.get("paragraph") or {}
                        for pedaco in paragrafo_celula.get("elements") or []:
                            texto = (pedaco.get("textRun") or {}).get("content")
                            if texto:
                                partes.append(texto)

    return "".join(partes)


def indice_final(documento: dict) -> int:
    """Índice seguro para inserir texto ao final do corpo do documento."""
    conteudo = (documento.get("body") or {}).get("content") or []
    if not conteudo:
        return 1

    ultimo = conteudo[-1]
    fim = int(ultimo.get("endIndex") or 1)
    # O último caractere é o "\n" final da seção; inserimos antes dele.
    return max(fim - 1, 1)


class DocsClient:
    """Cliente mínimo da Docs API v1."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        auth: GoogleAuth | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.Client(timeout=30.0)
        self._sleep = sleep
        self._auth = auth or GoogleAuth(settings, client=self._client, sleep=sleep)

    # ------------------------------------------------------------------
    # Autenticação / HTTP
    # ------------------------------------------------------------------
    def _access_token(self) -> str:
        try:
            return self._auth.access_token(ESCOPO_DOCS)
        except GoogleAuthError as exc:
            raise DocsError(str(exc)) from exc

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _enviar(self, metodo: str, url: str, **kwargs) -> dict:
        try:
            resposta = self._client.request(
                metodo, url, headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise DocsError(f"Falha de rede ao acessar o Docs: {exc}") from exc

        if resposta.status_code >= 400:
            raise DocsError(
                f"Docs HTTP {resposta.status_code}: {resposta.text[:300]}"
            )
        return resposta.json() if resposta.content else {}

    # ------------------------------------------------------------------
    # Operações
    # ------------------------------------------------------------------
    def obter_documento(self, document_id: str) -> dict:
        """Baixa a estrutura completa do documento."""
        if not document_id:
            raise DocsError("ID do documento não informado.")
        return self._enviar("GET", f"{DOCS_API}/{document_id}")

    def ler_texto(self, document_id: str) -> str:
        """Devolve o texto corrido do documento."""
        return extrair_texto(self.obter_documento(document_id))

    def criar_documento(self, titulo: str) -> dict:
        """Cria um documento vazio com o título informado."""
        return self._enviar("POST", DOCS_API, json={"title": titulo})

    def anexar_texto(self, document_id: str, texto: str) -> dict:
        """Insere o texto ao final do corpo do documento."""
        if not texto:
            return {}

        documento = self.obter_documento(document_id)
        indice = indice_final(documento)

        return self.aplicar_requisicoes(
            document_id,
            [
                {
                    "insertText": {
                        "location": {"index": indice},
                        "text": texto,
                    }
                }
            ],
        )

    def substituir_texto(
        self, document_id: str, de: str, para: str, *, diferenciar_maiusculas: bool = True
    ) -> dict:
        """Substitui todas as ocorrências de um trecho."""
        if not de:
            raise DocsError("Trecho a substituir não informado.")

        return self.aplicar_requisicoes(
            document_id,
            [
                {
                    "replaceAllText": {
                        "containsText": {
                            "text": de,
                            "matchCase": diferenciar_maiusculas,
                        },
                        "replaceText": para,
                    }
                }
            ],
        )

    def aplicar_requisicoes(self, document_id: str, requisicoes: list[dict]) -> dict:
        """Aplica um lote de requisições ``batchUpdate``."""
        if not document_id:
            raise DocsError("ID do documento não informado.")
        return self._enviar(
            "POST",
            f"{DOCS_API}/{document_id}:batchUpdate",
            json={"requests": requisicoes},
        )
