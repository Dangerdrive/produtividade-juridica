"""Normalização e validação dos dados devolvidos pelo modelo.

Regra de ouro: **nunca confiar apenas na saída do LLM**. Datas e números de
processo são reinterpretados aqui, e o que não for validável é preservado como
texto bruto + status ``REVISAR`` (para triagem humana).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.modules.prazos.models import StatusExtracao

#: Padrão CNJ: NNNNNNN-NN.NNNN.N.NN.NNNN
RE_PROCESSO_CNJ = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")

RE_DATA_ISO = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
RE_DATA_BR = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$")

_LIXO = re.compile(r"^(null|undefined|n/a|na|-|—)$", re.IGNORECASE)

TIPO_NAO_IDENTIFICADO = "Não identificado"
ADVOGADO_NAO_IDENTIFICADO = "Não identificado"


@dataclass(slots=True)
class PrazoExtraido:
    """Prazo já normalizado, pronto para gravação."""

    advogado: str
    tipo_prazo: str
    data_prazo: date | None
    numero_processo: str
    status: str
    #: Valor original devolvido pelo modelo quando a data não é interpretável.
    data_bruta: str = ""

    @property
    def completo(self) -> bool:
        return bool(
            self.advogado
            and self.advogado != ADVOGADO_NAO_IDENTIFICADO
            and self.data_prazo
            and RE_PROCESSO_CNJ.match(self.numero_processo or "")
        )


def texto_limpo(valor: object) -> str:
    """Converte um campo do modelo em string utilizável."""
    if valor is None:
        return ""
    texto = str(valor).strip()
    if not texto or _LIXO.match(texto):
        return ""
    return texto


def normalizar_data(valor: object) -> tuple[date | None, str]:
    """Interpreta a data devolvida pelo modelo.

    Aceita ``YYYY-MM-DD``, ``DD/MM/YYYY``, ``YYYY/MM/DD`` e ``DD-MM-YYYY``.

    Returns:
        ``(data, texto_bruto)``. Se não for interpretável, ``data`` é ``None`` e
        ``texto_bruto`` mantém o valor original.
    """
    texto = texto_limpo(valor)
    if not texto:
        return None, ""

    iso = RE_DATA_ISO.match(texto)
    br = RE_DATA_BR.match(texto)

    if iso:
        ano, mes, dia = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
    elif br:
        dia, mes, ano = int(br.group(1)), int(br.group(2)), int(br.group(3))
    else:
        return None, texto

    if not (1990 <= ano <= 2100) or not (1 <= mes <= 12) or not (1 <= dia <= 31):
        return None, texto

    try:
        return date(ano, mes, dia), ""
    except ValueError:
        # 31/02/2026 etc.
        return None, texto


def normalizar_processo(valor: object) -> tuple[str, bool]:
    """Remove espaços e valida o padrão CNJ."""
    texto = texto_limpo(valor)
    if not texto:
        return "", False

    normalizado = re.sub(r"\s+", "", texto)
    return normalizado, bool(RE_PROCESSO_CNJ.match(normalizado))


def normalizar_prazo(bruto: object) -> PrazoExtraido | None:
    """Converte um item bruto do modelo em :class:`PrazoExtraido`."""
    if not isinstance(bruto, dict):
        return None

    advogado = texto_limpo(bruto.get("advogado")) or ADVOGADO_NAO_IDENTIFICADO
    tipo = texto_limpo(bruto.get("tipo_prazo")) or TIPO_NAO_IDENTIFICADO
    data, data_bruta = normalizar_data(bruto.get("data"))
    processo, processo_valido = normalizar_processo(bruto.get("numero_processo"))

    completo = bool(
        advogado != ADVOGADO_NAO_IDENTIFICADO and data and processo_valido
    )

    return PrazoExtraido(
        advogado=advogado[:255],
        tipo_prazo=tipo[:120],
        data_prazo=data,
        numero_processo=processo[:40],
        status=StatusExtracao.OK.value if completo else StatusExtracao.REVISAR.value,
        data_bruta=data_bruta,
    )


def normalizar_prazos(payload: object) -> list[PrazoExtraido]:
    """Valida a estrutura ``{"prazos": [...]}``, normaliza e remove duplicados.

    Raises:
        ValueError: se o payload não tiver o array ``prazos``.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("prazos"), list):
        raise ValueError('Resposta sem o array "prazos".')

    vistos: set[tuple[str, str, str, str]] = set()
    prazos: list[PrazoExtraido] = []

    for item in payload["prazos"]:
        prazo = normalizar_prazo(item)
        if prazo is None:
            continue

        chave = (
            prazo.advogado.casefold(),
            prazo.tipo_prazo.casefold(),
            prazo.data_prazo.isoformat() if prazo.data_prazo else prazo.data_bruta,
            prazo.numero_processo,
        )
        if chave in vistos:
            continue

        vistos.add(chave)
        prazos.append(prazo)

    return prazos
