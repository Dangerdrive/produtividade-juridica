"""Cliente do Gmail via REST + OAuth2 (refresh token).

Optamos por chamadas HTTP diretas (``httpx``) em vez do SDK ``google-api-python-client``
para reduzir dependências e o tempo de *cold start* nas funções da Vercel.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from html.parser import HTMLParser

import httpx

from app.core.config import Settings
from app.modules.google.contas import (
    ContasError,
    GmailConta,
    obter_conta,
)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

_RE_ESPACOS = re.compile(r"[ \t\u00a0]+")
_RE_LINHAS_VAZIAS = re.compile(r"\n{3,}")


class GmailError(Exception):
    """Falha ao consultar a API do Gmail."""


@dataclass(slots=True)
class MensagemEmail:
    """Mensagem já convertida para o formato interno."""

    id: str
    thread_id: str | None
    remetente_nome: str
    remetente_email: str
    assunto: str
    data_recebimento: datetime  # UTC, sem tzinfo
    corpo: str


@dataclass(slots=True)
class PaginaMensagens:
    ids: list[str]
    proximo_page_token: str | None = None


class _ExtratorTexto(HTMLParser):
    """Extrai texto visível de um HTML, ignorando script/style."""

    _IGNORAR = {"script", "style", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._ignorando = 0

    def handle_starttag(self, tag, attrs):  # noqa: D102
        if tag in self._IGNORAR:
            self._ignorando += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self._partes.append("\n")

    def handle_endtag(self, tag):  # noqa: D102
        if tag in self._IGNORAR and self._ignorando:
            self._ignorando -= 1
        elif tag in ("p", "div", "tr"):
            self._partes.append("\n")

    def handle_data(self, data):  # noqa: D102
        if not self._ignorando:
            self._partes.append(data)

    def texto(self) -> str:
        return "".join(self._partes)


def html_para_texto(html: str) -> str:
    """Converte HTML em texto simples."""
    parser = _ExtratorTexto()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTML malformado é tolerado
        return re.sub(r"<[^>]+>", " ", html)
    return parser.texto()


def limpar_texto(texto: str) -> str:
    """Normaliza espaços em branco mantendo as quebras de linha."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = "\n".join(_RE_ESPACOS.sub(" ", linha).strip() for linha in texto.split("\n"))
    texto = _RE_LINHAS_VAZIAS.sub("\n\n", texto)
    return texto.strip()


def decodificar_header(valor: str | None) -> str:
    """Decodifica cabeçalhos MIME (``=?UTF-8?B?...?=``)."""
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except Exception:  # pragma: no cover - cabeçalho malformado
        return valor


def separar_remetente(from_header: str | None) -> tuple[str, str]:
    """Separa ``"Nome <email>"`` em ``(nome, email)``."""
    bruto = (from_header or "").strip()
    if not bruto:
        return "", ""

    nome, email = parseaddr(bruto)
    nome = nome.strip() or (email.split("@")[0] if email else bruto)
    return nome, email.strip().lower()


def _decodificar_base64url(dados: str) -> bytes:
    return base64.urlsafe_b64decode(dados + "=" * (-len(dados) % 4))


def montar_mensagem_raw(
    *,
    para: str,
    assunto: str,
    corpo: str,
    de: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    html: bool = False,
) -> str:
    """Monta o campo ``raw`` (base64url) aceito por ``messages.send``."""
    mensagem = EmailMessage()
    mensagem["To"] = para
    mensagem["Subject"] = assunto
    if de:
        mensagem["From"] = de
    if cc:
        mensagem["Cc"] = cc
    if bcc:
        mensagem["Bcc"] = bcc

    if html:
        mensagem.set_content("Este e-mail requer um leitor com suporte a HTML.")
        mensagem.add_alternative(corpo, subtype="html")
    else:
        mensagem.set_content(corpo)

    return base64.urlsafe_b64encode(mensagem.as_bytes()).decode("ascii").rstrip("=")


def decodificar_mensagem_raw(raw: str) -> EmailMessage:
    """Decodifica o ``raw`` (útil para depuração/testes)."""
    return BytesParser(policy=policy.default).parsebytes(
        _decodificar_base64url(raw)
    )


def _coletar_corpo(payload: dict) -> str:
    """Percorre a árvore de partes e devolve o melhor texto disponível."""
    textuais: list[str] = []
    html: list[str] = []

    def visitar(parte: dict) -> None:
        mime = (parte.get("mimeType") or "").lower()
        corpo = parte.get("body") or {}
        dados = corpo.get("data")

        if dados and mime == "text/plain":
            textuais.append(_decodificar_base64url(dados).decode("utf-8", "replace"))
        elif dados and mime == "text/html":
            html.append(_decodificar_base64url(dados).decode("utf-8", "replace"))

        for filha in parte.get("parts") or []:
            visitar(filha)

    visitar(payload or {})

    if textuais:
        return limpar_texto("\n".join(textuais))
    if html:
        return limpar_texto(html_para_texto("\n".join(html)))
    return ""


class GmailClient:
    """Cliente leve da Gmail API."""

    def __init__(
        self,
        settings: Settings,
        *,
        conta: str | GmailConta | None = None,
        client: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings

        try:
            self.conta = (
                conta if isinstance(conta, GmailConta) else obter_conta(settings, conta)
            )
        except ContasError as exc:
            raise GmailError(str(exc)) from exc

        self.nome_conta = self.conta.nome
        self._client = client or httpx.Client(timeout=30.0)
        self._sleep = sleep
        self._token: str | None = None
        self._token_expira_em: float = 0.0

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------
    def _access_token(self) -> str:
        agora = time.monotonic()
        if self._token and agora < self._token_expira_em:
            return self._token

        if not self.conta.configurada:
            raise GmailError(
                f"Credenciais da conta Gmail '{self.conta.nome}' ausentes "
                "(client_id, client_secret ou refresh_token)."
            )

        resposta = self._client.post(
            OAUTH_TOKEN_URL,
            data={
                "client_id": self.conta.client_id,
                "client_secret": self.conta.client_secret,
                "refresh_token": self.conta.refresh_token,
                "grant_type": "refresh_token",
            },
        )

        if resposta.status_code != 200:
            raise GmailError(
                f"Falha ao renovar o token do Gmail (HTTP {resposta.status_code}): "
                f"{resposta.text[:200]}"
            )

        dados = resposta.json()
        token = dados.get("access_token")
        if not token:
            raise GmailError("Resposta de token sem 'access_token'.")

        self._token = token
        # Renova 60 s antes do vencimento.
        self._token_expira_em = agora + max(int(dados.get("expires_in", 3600)) - 60, 30)
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _get(self, caminho: str, params: dict | None = None) -> dict:
        try:
            resposta = self._client.get(
                f"{GMAIL_API}{caminho}", params=params or {}, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise GmailError(f"Falha de rede ao consultar o Gmail: {exc}") from exc

        if resposta.status_code != 200:
            raise GmailError(
                f"Gmail HTTP {resposta.status_code}: {resposta.text[:200]}"
            )
        return resposta.json()

    # ------------------------------------------------------------------
    # Operações
    # ------------------------------------------------------------------
    def listar_mensagens(
        self,
        query: str | None = None,
        *,
        max_results: int = 25,
        page_token: str | None = None,
    ) -> PaginaMensagens:
        """Lista IDs de mensagens (``format=MINIMAL`` implícito)."""
        query = query or self.conta.query
        params: dict[str, object] = {"q": query, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token

        dados = self._get("/messages", params)
        ids = [item["id"] for item in dados.get("messages", []) if item.get("id")]
        return PaginaMensagens(ids=ids, proximo_page_token=dados.get("nextPageToken"))

    def perfil(self) -> dict:
        """Dados da conta autenticada (endereço, totais)."""
        dados = self._get("/profile")
        return {
            "email": dados.get("emailAddress", ""),
            "messages_total": dados.get("messagesTotal", 0),
            "threads_total": dados.get("threadsTotal", 0),
        }

    def obter_mensagem(self, message_id: str) -> MensagemEmail:
        """Baixa e converte uma mensagem completa."""
        bruto = self._get(f"/messages/{message_id}", {"format": "full"})
        return self._converter(bruto)

    def marcar_como_lida(self, message_id: str) -> bool:
        """Remove o rótulo UNREAD. Falhas não interrompem a sincronização."""
        try:
            resposta = self._client.post(
                f"{GMAIL_API}/messages/{message_id}/modify",
                json={"removeLabelIds": ["UNREAD"]},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise GmailError(f"Falha ao marcar como lida: {exc}") from exc

        return resposta.status_code == 200

    def enviar_mensagem(
        self,
        *,
        para: str,
        assunto: str,
        corpo: str,
        cc: str | None = None,
        bcc: str | None = None,
        html: bool = False,
    ) -> dict:
        """Envia um e-mail e devolve a resposta da API."""
        raw = montar_mensagem_raw(
            para=para,
            assunto=assunto,
            corpo=corpo,
            cc=cc,
            bcc=bcc,
            html=html,
        )

        try:
            resposta = self._client.post(
                f"{GMAIL_API}/messages/send",
                json={"raw": raw},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise GmailError(f"Falha ao enviar e-mail: {exc}") from exc

        if resposta.status_code >= 400:
            raise GmailError(
                f"Gmail HTTP {resposta.status_code}: {resposta.text[:200]}"
            )
        return resposta.json()

    # ------------------------------------------------------------------
    # Conversão
    # ------------------------------------------------------------------
    @staticmethod
    def _converter(bruto: dict) -> MensagemEmail:
        payload = bruto.get("payload") or {}
        headers = {
            (h.get("name") or "").lower(): h.get("value") or ""
            for h in payload.get("headers") or []
        }

        nome, email = separar_remetente(headers.get("from"))
        assunto = decodificar_header(headers.get("subject"))

        interno = bruto.get("internalDate")
        if interno:
            data = datetime.fromtimestamp(int(interno) / 1000, tz=UTC)
        else:
            data = datetime.now(UTC)

        corpo = _coletar_corpo(payload)
        if not corpo:
            corpo = limpar_texto(bruto.get("snippet") or "")

        return MensagemEmail(
            id=bruto.get("id", ""),
            thread_id=bruto.get("threadId"),
            remetente_nome=nome,
            remetente_email=email,
            assunto=assunto,
            data_recebimento=data.replace(tzinfo=None),
            corpo=corpo,
        )
