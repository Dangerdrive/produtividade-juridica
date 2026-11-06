"""Consultas e agregações que alimentam o dashboard.

Substitui a "Tabela Dinâmica" da planilha por agregações SQL — mais previsíveis,
versionáveis e testáveis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Email, Prazo, StatusExtracao


def para_local(dt: datetime | None, tz: str) -> datetime | None:
    """Converte um datetime UTC naive para o fuso de exibição."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))


# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Resumo:
    total_emails: int = 0
    total_prazos: int = 0
    processos_distintos: int = 0
    emails_pendentes: int = 0
    emails_com_erro: int = 0
    prazos_sem_data: int = 0
    ultima_sincronizacao: datetime | None = None


def resumo(db: Session) -> Resumo:
    """Números do topo do dashboard."""
    contagem_status = dict(
        db.execute(select(Email.status, func.count(Email.id)).group_by(Email.status)).all()
    )

    total_prazos = db.scalar(select(func.count(Prazo.id))) or 0
    processos = db.scalar(
        select(func.count(func.distinct(func.nullif(Prazo.numero_processo, ""))))
    ) or 0
    sem_data = db.scalar(select(func.count(Prazo.id)).where(Prazo.data_prazo.is_(None))) or 0
    ultima = db.scalar(select(func.max(Email.processado_em)))

    finais = sum(
        contagem_status.get(status.value, 0)
        for status in (StatusExtracao.OK, StatusExtracao.REVISAR, StatusExtracao.SEM_PRAZO)
    )
    erros = contagem_status.get(StatusExtracao.ERRO_IA.value, 0) + contagem_status.get(
        StatusExtracao.ERRO_JSON.value, 0
    )
    total_emails = sum(contagem_status.values())

    return Resumo(
        total_emails=total_emails,
        total_prazos=total_prazos,
        processos_distintos=processos,
        emails_pendentes=max(total_emails - finais, 0),
        emails_com_erro=erros,
        prazos_sem_data=sem_data,
        ultima_sincronizacao=ultima,
    )


# ---------------------------------------------------------------------------
# Matriz Advogado x Tipo de Prazo
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Matriz:
    advogados: list[str] = field(default_factory=list)
    tipos: list[str] = field(default_factory=list)
    valores: dict[tuple[str, str], int] = field(default_factory=dict)
    total_por_advogado: dict[str, int] = field(default_factory=dict)
    total_por_tipo: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def valor(self, advogado: str, tipo: str) -> int:
        return self.valores.get((advogado, tipo), 0)


def matriz_advogado_tipo(
    db: Session,
    *,
    inicio: date | None = None,
    fim: date | None = None,
) -> Matriz:
    """Contagem de processos por advogado (linhas) e tipo de prazo (colunas)."""
    consulta = (
        select(
            Prazo.advogado,
            Prazo.tipo_prazo,
            func.count(func.nullif(Prazo.numero_processo, "")).label("quantidade"),
        )
        .group_by(Prazo.advogado, Prazo.tipo_prazo)
        .order_by(Prazo.advogado, Prazo.tipo_prazo)
    )
    consulta = _filtrar_periodo(consulta, Prazo, inicio, fim)

    matriz = Matriz()

    for advogado, tipo, quantidade in db.execute(consulta).all():
        advogado = advogado or "Não informado"
        tipo = tipo or "Não informado"
        quantidade = int(quantidade or 0)

        matriz.valores[(advogado, tipo)] = quantidade
        matriz.total_por_advogado[advogado] = matriz.total_por_advogado.get(advogado, 0) + quantidade
        matriz.total_por_tipo[tipo] = matriz.total_por_tipo.get(tipo, 0) + quantidade
        matriz.total += quantidade

    matriz.advogados = sorted(
        matriz.total_por_advogado, key=lambda nome: (-matriz.total_por_advogado[nome], nome)
    )
    matriz.tipos = sorted(
        matriz.total_por_tipo, key=lambda nome: (-matriz.total_por_tipo[nome], nome)
    )
    return matriz


# ---------------------------------------------------------------------------
# Distribuições
# ---------------------------------------------------------------------------
def distribuicao_por_tipo(
    db: Session, *, inicio: date | None = None, fim: date | None = None
) -> list[tuple[str, int]]:
    consulta = (
        select(Prazo.tipo_prazo, func.count(Prazo.id))
        .group_by(Prazo.tipo_prazo)
        .order_by(func.count(Prazo.id).desc())
    )
    consulta = _filtrar_periodo(consulta, Prazo, inicio, fim)
    return [(tipo or "Não informado", int(qtd)) for tipo, qtd in db.execute(consulta).all()]


def distribuicao_por_status(db: Session) -> list[tuple[str, int]]:
    consulta = (
        select(Email.status, func.count(Email.id))
        .group_by(Email.status)
        .order_by(func.count(Email.id).desc())
    )
    return [(status, int(qtd)) for status, qtd in db.execute(consulta).all()]


def prazos_por_mes(
    db: Session, *, meses: int = 6
) -> list[tuple[str, int]]:
    """Quantidade de prazos agrupada por ``YYYY-MM``."""
    chave = func.substr(cast(Prazo.data_prazo, String), 1, 7)
    consulta = (
        select(chave.label("mes"), func.count(Prazo.id))
        .where(Prazo.data_prazo.is_not(None))
        .group_by(chave)
        .order_by(chave.desc())
        .limit(meses)
    )
    linhas = db.execute(consulta).all()
    return [(mes, int(qtd)) for mes, qtd in reversed(linhas)]


# ---------------------------------------------------------------------------
# Listagens
# ---------------------------------------------------------------------------
def proximos_prazos(db: Session, *, dias: int = 30, limite: int = 20) -> list[Prazo]:
    """Prazos com vencimento entre hoje e hoje+``dias``."""
    hoje = datetime.now(UTC).date()
    consulta = (
        select(Prazo)
        .options(selectinload(Prazo.email))
        .where(Prazo.data_prazo.is_not(None))
        .where(Prazo.data_prazo >= hoje)
        .where(Prazo.data_prazo <= date.fromordinal(hoje.toordinal() + dias))
        .order_by(Prazo.data_prazo, Prazo.advogado)
        .limit(limite)
    )
    return list(db.scalars(consulta).all())


def prazos_vencidos(db: Session, *, limite: int = 20) -> list[Prazo]:
    """Prazos com data no passado (possível atraso de cadastro)."""
    hoje = datetime.now(UTC).date()
    consulta = (
        select(Prazo)
        .options(selectinload(Prazo.email))
        .where(Prazo.data_prazo.is_not(None))
        .where(Prazo.data_prazo < hoje)
        .order_by(Prazo.data_prazo.desc())
        .limit(limite)
    )
    return list(db.scalars(consulta).all())


@dataclass(slots=True)
class PaginaPrazos:
    itens: list[Prazo]
    total: int
    pagina: int
    por_pagina: int

    @property
    def total_paginas(self) -> int:
        return max((self.total + self.por_pagina - 1) // self.por_pagina, 1)

    @property
    def tem_anterior(self) -> bool:
        return self.pagina > 1

    @property
    def tem_proxima(self) -> bool:
        return self.pagina < self.total_paginas


def consultar_prazos(
    db: Session,
    *,
    advogado: str | None = None,
    tipo: str | None = None,
    status: str | None = None,
    inicio: date | None = None,
    fim: date | None = None,
    texto: str | None = None,
    pagina: int = 1,
    por_pagina: int = 50,
) -> PaginaPrazos:
    """Lista paginada de prazos com filtros."""
    filtros = []

    if advogado:
        filtros.append(Prazo.advogado == advogado)
    if tipo:
        filtros.append(Prazo.tipo_prazo == tipo)
    if status:
        filtros.append(Prazo.status == status)
    if inicio:
        filtros.append(Prazo.data_prazo >= inicio)
    if fim:
        filtros.append(Prazo.data_prazo <= fim)
    if texto:
        alvo = f"%{texto.strip()}%"
        filtros.append(
            or_(
                Prazo.numero_processo.ilike(alvo),
                Prazo.advogado.ilike(alvo),
                Prazo.tipo_prazo.ilike(alvo),
            )
        )

    base = select(Prazo).where(*filtros) if filtros else select(Prazo)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    pagina = max(pagina, 1)
    itens = list(
        db.scalars(
            base.options(selectinload(Prazo.email))
            .order_by(Prazo.data_prazo.is_(None), Prazo.data_prazo, Prazo.id)
            .offset((pagina - 1) * por_pagina)
            .limit(por_pagina)
        ).all()
    )

    return PaginaPrazos(itens=itens, total=total, pagina=pagina, por_pagina=por_pagina)


# ---------------------------------------------------------------------------
def _filtrar_periodo(
    consulta: Select, modelo, inicio: date | None, fim: date | None
) -> Select:
    if inicio:
        consulta = consulta.where(modelo.data_prazo >= inicio)
    if fim:
        consulta = consulta.where(modelo.data_prazo <= fim)
    return consulta
