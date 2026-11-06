"""Espelhamento dos prazos numa planilha do Google Sheets.

A planilha é uma **projeção** do banco: a fonte da verdade continua sendo o
Postgres, e a aba `DB_Prazos` existe para alimentar o dashboard em Apps Script.

Autenticação: **conta de serviço** (não exige interação do usuário).
Basta compartilhar a planilha com o e-mail da conta de serviço como Editor.

    GOOGLE_SERVICE_ACCOUNT_JSON = conteúdo do JSON (cru ou base64)
    GOOGLE_SHEET_ID             = id da planilha
    GOOGLE_SHEET_TAB            = DB_Prazos (padrão)

Para não depender do SDK oficial, o JWT da conta de serviço é assinado com
``PyJWT`` e trocado por um access token via ``httpx``.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

import httpx
import jwt
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings
from app.models import Prazo, utcnow
from app.services.reports import para_local

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
ESCOPO = "https://www.googleapis.com/auth/spreadsheets"

#: Cabeçalho da aba de dados (ordem = colunas A..L).
CABECALHOS_PLANILHA: tuple[str, ...] = (
    "ID do Prazo",
    "Número do Processo",
    "Advogado",
    "Tipo de Prazo",
    "Data do Prazo",
    "Status",
    "Data Recebimento (E-mail)",
    "Remetente (E-mail)",
    "Remetente (Nome)",
    "Assunto",
    "Gmail Message ID",
    "Atualizado Em",
)

#: Coluna onde fica a chave de idempotência (ID do Prazo).
COLUNA_CHAVE = "A"
ULTIMA_COLUNA = "L"

_RE_RANGE = re.compile(r"![A-Z]+(\d+)")


class SheetsError(Exception):
    """Falha na comunicação com a API do Google Sheets."""


# ---------------------------------------------------------------------------
# Serialização
# ---------------------------------------------------------------------------
def linha_prazo(prazo: Prazo, fuso: str = "America/Sao_Paulo") -> list[str]:
    """Monta uma linha da aba DB_Prazos."""
    email = prazo.email
    recebido = para_local(email.data_recebimento, fuso) if email else None
    atualizado = para_local(prazo.atualizado_em or utcnow(), fuso)

    return [
        str(prazo.id),
        prazo.numero_processo or "",
        prazo.advogado or "",
        prazo.tipo_prazo or "",
        prazo.data_prazo.isoformat() if prazo.data_prazo else "",
        prazo.status or "",
        recebido.strftime("%Y-%m-%d %H:%M") if recebido else "",
        email.remetente_email if email else "",
        email.remetente_nome if email else "",
        (email.assunto if email else "")[:500],
        email.gmail_message_id if email else "",
        atualizado.strftime("%Y-%m-%d %H:%M") if atualizado else "",
    ]


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
class SheetsClient:
    """Cliente mínimo da Sheets API v4."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self.aba = settings.google_sheet_tab
        self._client = client or httpx.Client(timeout=30.0)
        self._sleep = sleep
        self._token: str | None = None
        self._expira_em: float = 0.0

    # -- autenticação ---------------------------------------------------
    def _credenciais(self) -> dict:
        bruto = (self.settings.google_service_account_json or "").strip()
        if not bruto:
            raise SheetsError("GOOGLE_SERVICE_ACCOUNT_JSON não configurado.")

        if not bruto.startswith("{"):
            try:
                bruto = base64.b64decode(bruto).decode("utf-8")
            except Exception as exc:  # noqa: BLE001
                raise SheetsError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON não é JSON nem base64 válido."
                ) from exc

        try:
            info = json.loads(bruto)
        except json.JSONDecodeError as exc:
            raise SheetsError("GOOGLE_SERVICE_ACCOUNT_JSON inválido.") from exc

        for chave in ("client_email", "private_key"):
            if not info.get(chave):
                raise SheetsError(f"Credencial sem '{chave}'.")
        return info

    def _access_token(self) -> str:
        agora = time.monotonic()
        if self._token and agora < self._expira_em:
            return self._token

        info = self._credenciais()
        token_uri = info.get("token_uri", "https://oauth2.googleapis.com/token")
        emitido_em = int(time.time())

        claims = {
            "iss": info["client_email"],
            "scope": ESCOPO,
            "aud": token_uri,
            "iat": emitido_em,
            "exp": emitido_em + 3600,
        }

        try:
            assertion = jwt.encode(claims, info["private_key"], algorithm="RS256")
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Falha ao assinar o JWT da conta de serviço: {exc}") from exc

        resposta = self._client.post(
            token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )

        if resposta.status_code != 200:
            raise SheetsError(
                f"Falha ao obter token do Sheets (HTTP {resposta.status_code}): "
                f"{resposta.text[:200]}"
            )

        dados = resposta.json()
        token = dados.get("access_token")
        if not token:
            raise SheetsError("Resposta de token sem 'access_token'.")

        self._token = token
        self._expira_em = agora + max(int(dados.get("expires_in", 3600)) - 60, 30)
        return token

    # -- HTTP -----------------------------------------------------------
    def _url(self, sufixo: str) -> str:
        if not self.settings.google_sheet_id:
            raise SheetsError("GOOGLE_SHEET_ID não configurado.")
        return f"{SHEETS_API}/{self.settings.google_sheet_id}{sufixo}"

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

    # -- operações ------------------------------------------------------
    def _range(self, referencia: str) -> str:
        return f"'{self.aba}'!{referencia}"

    def ler_valores(self, referencia: str) -> list[list[str]]:
        """Lê um intervalo (``A1:L1``, ``A2:A``...)."""
        alvo = quote(self._range(referencia), safe="")
        dados = self._enviar("GET", f"/values/{alvo}")
        return dados.get("values", [])

    def escrever_valores(self, referencia: str, valores: list[list[str]]) -> dict:
        """Sobrescreve um intervalo."""
        alvo = quote(self._range(referencia), safe="")
        return self._enviar(
            "PUT",
            f"/values/{alvo}",
            params={"valueInputOption": "USER_ENTERED"},
            json={"values": valores},
        )

    def anexar(self, valores: list[list[str]]) -> str:
        """Anexa linhas ao final da aba. Devolve o intervalo atualizado."""
        alvo = quote(self._range(f"{COLUNA_CHAVE}1:{ULTIMA_COLUNA}"), safe="")
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

    def atualizar_linhas(self, linhas: dict[int, list[str]]) -> None:
        """Atualiza várias linhas de uma vez (``{linha: valores}``)."""
        if not linhas:
            return

        dados = [
            {"range": self._range(f"A{n}:{ULTIMA_COLUNA}{n}"), "values": [valores]}
            for n, valores in sorted(linhas.items())
        ]

        self._enviar(
            "POST",
            "/values:batchUpdate",
            json={"valueInputOption": "USER_ENTERED", "data": dados},
        )

    def garantir_cabecalho(self) -> bool:
        """Cria/corrige a linha 1. Devolve ``True`` se precisou escrever."""
        esperado = list(CABECALHOS_PLANILHA)

        try:
            atual = (self.ler_valores(f"A1:{ULTIMA_COLUNA}1") or [[]])[0]
        except SheetsError:
            atual = []

        if [str(c).strip() for c in atual] == esperado:
            return False

        self.escrever_valores(f"A1:{ULTIMA_COLUNA}1", [esperado])
        return True

    def mapa_ids(self) -> dict[str, int]:
        """``{id_do_prazo: número_da_linha}`` a partir da coluna A."""
        valores = self.ler_valores(f"{COLUNA_CHAVE}2:{COLUNA_CHAVE}")
        mapa: dict[str, int] = {}

        for indice, linha in enumerate(valores, start=2):
            if not linha:
                continue
            chave = str(linha[0]).strip()
            if chave and chave not in mapa:
                mapa[chave] = indice

        return mapa


# ---------------------------------------------------------------------------
# Sincronização banco -> planilha
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ResultadoPlanilha:
    enviados: int = 0
    atualizados: int = 0
    ignorados: int = 0
    cabecalho_criado: bool = False
    erro: str | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.erro is None

    def as_dict(self) -> dict:
        return {
            "enviados": self.enviados,
            "atualizados": self.atualizados,
            "ignorados": self.ignorados,
            "cabecalho_criado": self.cabecalho_criado,
            "erro": self.erro,
            "ok": self.ok,
            "avisos": self.avisos[:10],
        }


def sincronizar_planilha(
    db: Session,
    settings: Settings,
    *,
    cliente: SheetsClient | None = None,
    limite: int = 500,
) -> ResultadoPlanilha:
    """Envia para o Sheets os prazos novos e os alterados."""
    resultado = ResultadoPlanilha()

    if not settings.sheets_configurado:
        resultado.erro = "Planilha não configurada (GOOGLE_SERVICE_ACCOUNT_JSON/GOOGLE_SHEET_ID)."
        return resultado

    sheets = cliente or SheetsClient(settings)

    try:
        resultado.cabecalho_criado = sheets.garantir_cabecalho()
    except SheetsError as exc:
        resultado.erro = str(exc)
        return resultado

    # --- novos ---------------------------------------------------------
    novos = list(
        db.scalars(
            select(Prazo)
            .options(joinedload(Prazo.email))
            .where(Prazo.sheet_linha.is_(None))
            .order_by(Prazo.id)
            .limit(limite)
        ).all()
    )

    agora = utcnow()

    if novos:
        try:
            sheets.anexar([linha_prazo(p, settings.timezone) for p in novos])
            mapa = sheets.mapa_ids()
        except SheetsError as exc:
            resultado.erro = str(exc)
            db.rollback()
            return resultado

        for prazo in novos:
            linha = mapa.get(str(prazo.id))
            if linha is None:
                resultado.avisos.append(f"prazo {prazo.id} não localizado na planilha")
                continue
            prazo.sheet_linha = linha
            prazo.sheet_sincronizado_em = agora
            resultado.enviados += 1

        db.commit()

    # --- alterados -----------------------------------------------------
    alterados = list(
        db.scalars(
            select(Prazo)
            .options(joinedload(Prazo.email))
            .where(Prazo.sheet_linha.is_not(None))
            .where(
                or_(
                    Prazo.sheet_sincronizado_em.is_(None),
                    Prazo.atualizado_em > Prazo.sheet_sincronizado_em,
                )
            )
            .order_by(Prazo.id)
            .limit(limite)
        ).all()
    )

    if alterados:
        linhas = {
            int(p.sheet_linha): linha_prazo(p, settings.timezone)
            for p in alterados
            if p.sheet_linha
        }
        try:
            sheets.atualizar_linhas(linhas)
        except SheetsError as exc:
            resultado.erro = str(exc)
            db.rollback()
            return resultado

        for prazo in alterados:
            prazo.sheet_sincronizado_em = agora
            resultado.atualizados += 1

        db.commit()

    resultado.ignorados = 0
    return resultado


def espelhar_apos_ingestao(db: Session, settings: Settings) -> ResultadoPlanilha | None:
    """Atalho usado pela ingestão: nunca deixa o Sheets derrubar a sincronização."""
    if not settings.sheets_sync_ativo or not settings.sheets_configurado:
        return None

    try:
        return sincronizar_planilha(db, settings)
    except Exception as exc:  # noqa: BLE001 - espelhamento é best-effort
        db.rollback()
        resultado = ResultadoPlanilha(erro=f"Falha inesperada: {exc}")
        return resultado


def agora_local(fuso: str) -> datetime:
    """Agora no fuso de exibição (utilitário para relatórios/CLI)."""
    momento = para_local(utcnow(), fuso)
    return momento or datetime.now()
