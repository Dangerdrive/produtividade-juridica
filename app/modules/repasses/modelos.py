"""Modelos de domínio do módulo de repasses.

A planilha é a fonte da verdade; estes objetos são a representação tipada de
uma linha, usada pelos serviços, pela API e pelos templates.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.modules.repasses.utils import formatar_valor, normalizar_chave


class StatusRepasse(str, enum.Enum):
    """Estados possíveis de um repasse."""

    PENDENTE = "PENDENTE"
    EM_ANDAMENTO = "EM_ANDAMENTO"
    CONCLUIDO = "CONCLUIDO"
    CANCELADO = "CANCELADO"

    @property
    def aberto(self) -> bool:
        """Indica se o repasse ainda exige acompanhamento."""
        return self in (StatusRepasse.PENDENTE, StatusRepasse.EM_ANDAMENTO)

    @property
    def rotulo(self) -> str:
        return ROTULOS_STATUS[self]


ROTULOS_STATUS: dict[StatusRepasse, str] = {
    StatusRepasse.PENDENTE: "Pendente",
    StatusRepasse.EM_ANDAMENTO: "Em andamento",
    StatusRepasse.CONCLUIDO: "Concluído",
    StatusRepasse.CANCELADO: "Cancelado",
}

_ALIASES_STATUS: dict[str, StatusRepasse] = {
    "pendente": StatusRepasse.PENDENTE,
    "a pagar": StatusRepasse.PENDENTE,
    "em aberto": StatusRepasse.PENDENTE,
    "aguardando": StatusRepasse.PENDENTE,
    "aberto": StatusRepasse.PENDENTE,
    "em andamento": StatusRepasse.EM_ANDAMENTO,
    "andamento": StatusRepasse.EM_ANDAMENTO,
    "processando": StatusRepasse.EM_ANDAMENTO,
    "em analise": StatusRepasse.EM_ANDAMENTO,
    "concluido": StatusRepasse.CONCLUIDO,
    "pago": StatusRepasse.CONCLUIDO,
    "finalizado": StatusRepasse.CONCLUIDO,
    "ok": StatusRepasse.CONCLUIDO,
    "cancelado": StatusRepasse.CANCELADO,
    "cancelada": StatusRepasse.CANCELADO,
    "estornado": StatusRepasse.CANCELADO,
}


def normalizar_status(valor: object) -> StatusRepasse:
    """Converte textos livres da planilha em um :class:`StatusRepasse`."""
    if isinstance(valor, StatusRepasse):
        return valor

    chave = normalizar_chave(valor)
    if not chave:
        return StatusRepasse.PENDENTE

    if chave in _ALIASES_STATUS:
        return _ALIASES_STATUS[chave]

    # Aceita também o próprio valor do enum ("EM_ANDAMENTO" -> "em andamento").
    for status in StatusRepasse:
        if normalizar_chave(status.value) == chave:
            return status

    return StatusRepasse.PENDENTE


@dataclass(slots=True)
class Repasse:
    """Uma linha da planilha de repasses."""

    id: str
    cliente: str
    processo: str = ""
    valor: Decimal | None = None
    data_prevista: date | None = None
    data_pagamento: date | None = None
    status: StatusRepasse = StatusRepasse.PENDENTE
    responsavel: str = ""
    observacoes: str = ""
    atualizado_em: datetime | None = None

    #: Linha física na planilha (1-based).
    linha: int | None = None
    #: Valores originais por cabeçalho, para preservar colunas desconhecidas.
    bruto: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def pendente(self) -> bool:
        return self.status.aberto

    @property
    def valor_formatado(self) -> str:
        return formatar_valor(self.valor) or "—"

    @property
    def status_css(self) -> str:
        return self.status.value.casefold()

    def dias_restantes(self, hoje: date | None = None) -> int | None:
        if self.data_prevista is None:
            return None
        return (self.data_prevista - (hoje or date.today())).days

    def atrasado(self, hoje: date | None = None) -> bool:
        dias = self.dias_restantes(hoje)
        return self.pendente and dias is not None and dias < 0

    def as_dict(self, hoje: date | None = None) -> dict:
        return {
            "id": self.id,
            "cliente": self.cliente,
            "processo": self.processo,
            "valor": float(self.valor) if self.valor is not None else None,
            "valor_formatado": formatar_valor(self.valor),
            "data_prevista": self.data_prevista.isoformat()
            if self.data_prevista
            else None,
            "data_pagamento": self.data_pagamento.isoformat()
            if self.data_pagamento
            else None,
            "status": self.status.value,
            "status_rotulo": self.status.rotulo,
            "responsavel": self.responsavel,
            "observacoes": self.observacoes,
            "pendente": self.pendente,
            "atrasado": self.atrasado(hoje),
            "dias_restantes": self.dias_restantes(hoje),
            "linha": self.linha,
        }


@dataclass(slots=True)
class ResumoRepasses:
    """Agregados de um conjunto de repasses."""

    total: int = 0
    pendentes: int = 0
    em_andamento: int = 0
    concluidos: int = 0
    cancelados: int = 0
    atrasados: int = 0
    valor_total: Decimal = Decimal("0")
    valor_aberto: Decimal = Decimal("0")
    valor_pago: Decimal = Decimal("0")

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "pendentes": self.pendentes,
            "em_andamento": self.em_andamento,
            "concluidos": self.concluidos,
            "cancelados": self.cancelados,
            "atrasados": self.atrasados,
            "valor_total": float(self.valor_total),
            "valor_aberto": float(self.valor_aberto),
            "valor_pago": float(self.valor_pago),
            "valor_total_formatado": formatar_valor(self.valor_total),
            "valor_aberto_formatado": formatar_valor(self.valor_aberto),
            "valor_pago_formatado": formatar_valor(self.valor_pago),
        }


@dataclass(slots=True)
class ResumoCliente:
    """Visão consolidada de um cliente."""

    cliente: str
    repasses: list[Repasse]
    resumo: ResumoRepasses
    proxima_data_prevista: date | None = None

    def as_dict(self, hoje: date | None = None) -> dict:
        return {
            "cliente": self.cliente,
            "resumo": self.resumo.as_dict(),
            "proxima_data_prevista": self.proxima_data_prevista.isoformat()
            if self.proxima_data_prevista
            else None,
            "repasses": [repasse.as_dict(hoje) for repasse in self.repasses],
        }
