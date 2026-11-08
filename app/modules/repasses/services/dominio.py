"""Regras de negócio do módulo de repasses.

Funções puras sobre listas de :class:`Repasse`, fáceis de testar sem Google.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.repasses.modelos import (
    Repasse,
    ResumoCliente,
    ResumoRepasses,
    StatusRepasse,
)
from app.modules.repasses.utils import normalizar_chave


def ordenar_pendentes(repasses: list[Repasse]) -> list[Repasse]:
    """Pendentes por vencimento (sem data por último)."""
    return sorted(
        repasses,
        key=lambda repasse: (
            repasse.data_prevista is None,
            repasse.data_prevista or date.max,
            repasse.cliente.casefold(),
            repasse.id,
        ),
    )


def filtrar(
    repasses: list[Repasse],
    *,
    status: str | None = None,
    cliente: str | None = None,
    apenas_pendentes: bool = False,
    texto: str | None = None,
) -> list[Repasse]:
    """Filtros usados pela API e pela página interna."""
    resultado = list(repasses)

    if status:
        alvo = normalizar_chave(status)
        resultado = [
            repasse
            for repasse in resultado
            if normalizar_chave(repasse.status.value) == alvo
            or normalizar_chave(repasse.status.rotulo) == alvo
        ]

    if cliente:
        alvo = normalizar_chave(cliente)
        resultado = [
            repasse
            for repasse in resultado
            if alvo in normalizar_chave(repasse.cliente)
        ]

    if apenas_pendentes:
        resultado = [repasse for repasse in resultado if repasse.pendente]

    if texto:
        alvo = normalizar_chave(texto)
        resultado = [
            repasse
            for repasse in resultado
            if alvo in normalizar_chave(repasse.cliente)
            or alvo in normalizar_chave(repasse.processo)
            or alvo in normalizar_chave(repasse.responsavel)
            or alvo in normalizar_chave(repasse.observacoes)
            or alvo in normalizar_chave(repasse.id)
        ]

    return resultado


def pendentes(repasses: list[Repasse], *, hoje: date | None = None) -> list[Repasse]:
    """Repasses em aberto, ordenados por vencimento."""
    return ordenar_pendentes(
        [repasse for repasse in repasses if repasse.pendente]
    )


def atrasados(repasses: list[Repasse], *, hoje: date | None = None) -> list[Repasse]:
    """Pendentes cuja data prevista já passou."""
    return [
        repasse for repasse in pendentes(repasses, hoje=hoje)
        if repasse.atrasado(hoje)
    ]


def resumir(repasses: list[Repasse], *, hoje: date | None = None) -> ResumoRepasses:
    """Agrega contagens e valores."""
    resumo = ResumoRepasses(total=len(repasses))

    for repasse in repasses:
        valor = repasse.valor or Decimal("0")
        resumo.valor_total += valor

        if repasse.status is StatusRepasse.PENDENTE:
            resumo.pendentes += 1
        elif repasse.status is StatusRepasse.EM_ANDAMENTO:
            resumo.em_andamento += 1
        elif repasse.status is StatusRepasse.CONCLUIDO:
            resumo.concluidos += 1
        elif repasse.status is StatusRepasse.CANCELADO:
            resumo.cancelados += 1

        if repasse.pendente:
            resumo.valor_aberto += valor
            if repasse.atrasado(hoje):
                resumo.atrasados += 1
        elif repasse.status is StatusRepasse.CONCLUIDO:
            resumo.valor_pago += valor

    return resumo


def agrupar_por_cliente(repasses: list[Repasse]) -> dict[str, list[Repasse]]:
    """Agrupa preservando o nome original e ordenando por nome."""
    grupos: dict[str, list[Repasse]] = {}
    for repasse in repasses:
        grupos.setdefault(repasse.cliente, []).append(repasse)

    return {
        cliente: grupos[cliente]
        for cliente in sorted(grupos, key=lambda nome: nome.casefold())
    }


def resumo_cliente(
    repasses: list[Repasse], cliente: str, *, hoje: date | None = None
) -> ResumoCliente:
    """Consolida a visão de um cliente."""
    alvo = normalizar_chave(cliente)
    do_cliente = [
        repasse
        for repasse in repasses
        if normalizar_chave(repasse.cliente) == alvo
    ]

    ordenados = sorted(
        do_cliente,
        key=lambda repasse: (
            not repasse.pendente,
            repasse.data_prevista is None,
            repasse.data_prevista or date.max,
            repasse.id,
        ),
    )

    datas = [
        repasse.data_prevista
        for repasse in do_cliente
        if repasse.pendente and repasse.data_prevista is not None
    ]

    return ResumoCliente(
        cliente=(ordenados[0].cliente if ordenados else cliente),
        repasses=ordenados,
        resumo=resumir(do_cliente, hoje=hoje),
        proxima_data_prevista=min(datas) if datas else None,
    )


def resumos_por_cliente(
    repasses: list[Repasse], *, hoje: date | None = None
) -> list[ResumoCliente]:
    """Um :class:`ResumoCliente` por cliente, em ordem alfabética."""
    return [
        resumo_cliente(do_cliente, cliente, hoje=hoje)
        for cliente, do_cliente in agrupar_por_cliente(repasses).items()
    ]
