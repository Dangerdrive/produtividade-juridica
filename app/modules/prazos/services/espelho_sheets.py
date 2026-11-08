"""Espelhamento dos prazos numa planilha do Google Sheets.

A planilha é uma **projeção** do banco: a fonte da verdade continua sendo o
Postgres, e a aba ``DB_Prazos`` existe para alimentar o dashboard em Apps Script.

O acesso HTTP à API do Google fica no módulo ``app.modules.google``; aqui está
apenas a projeção específica do domínio de prazos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.modules.google.sheets import SheetsClient, SheetsError
from app.modules.prazos.models import Prazo, utcnow
from app.modules.prazos.services.reports import para_local

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


def criar_cliente(settings: Settings) -> SheetsClient:
    """Cliente de Sheets apontando para a planilha do módulo de prazos."""
    return SheetsClient(
        settings,
        sheet_id=settings.google_sheet_id,
        aba=settings.google_sheet_tab,
    )


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
        resultado.erro = (
            "Planilha não configurada (GOOGLE_SERVICE_ACCOUNT_JSON/GOOGLE_SHEET_ID)."
        )
        return resultado

    sheets = cliente or criar_cliente(settings)

    try:
        resultado.cabecalho_criado = sheets.garantir_cabecalho(
            CABECALHOS_PLANILHA, ultima_coluna=ULTIMA_COLUNA
        )
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
            sheets.anexar(
                [linha_prazo(p, settings.timezone) for p in novos],
                referencia=f"{COLUNA_CHAVE}1:{ULTIMA_COLUNA}",
            )
            mapa = sheets.mapa_ids(coluna=COLUNA_CHAVE)
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
            sheets.atualizar_linhas(linhas, ultima_coluna=ULTIMA_COLUNA)
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
        return ResultadoPlanilha(erro=f"Falha inesperada: {exc}")


def agora_local(fuso: str) -> datetime:
    """Agora no fuso de exibição (utilitário para relatórios/CLI)."""
    momento = para_local(utcnow(), fuso)
    return momento or datetime.now()
