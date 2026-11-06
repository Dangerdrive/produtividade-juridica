"""Extração determinística de informações a partir do texto do e-mail.

Aqui ficam as regras **sem IA**: o que dá para achar com regex confiável sai
daqui e é gravado antes de qualquer chamada ao modelo.

O número do processo é o caso mais importante: ele quase sempre está no
**assunto** no padrão CNJ (``0100138-89.2023.5.01.0074``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Número do processo (padrão CNJ: NNNNNNN-NN.NNNN.N.NN.NNNN)
# ---------------------------------------------------------------------------
#: Formato canônico, com separadores.
RE_PROCESSO_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")

#: Formato tolerante: aceita espaços e separadores ausentes/trocados.
#: Ex.: "0100138-89.2023.5.01.0074", "010013889.2023.5.01.0074",
#:      "0100138 89 2023 5 01 0074", "0100138-89.2023.501.0074"
RE_PROCESSO_TOLERANTE = re.compile(
    r"(?<!\d)"
    r"(\d{7})[\s\-\.]*(\d{2})[\s\-\.]*(\d{4})[\s\-\.]*(\d)[\s\-\.]*(\d{2})[\s\-\.]*(\d{4})"
    r"(?!\d)"
)

#: 20 dígitos corridos (colados), com limites de dígito.
RE_PROCESSO_SEM_SEPARADOR = re.compile(r"(?<!\d)(\d{20})(?!\d)")

# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------
RE_DATA_NUMERICA = re.compile(
    r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)"
)

MESES_PT = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

RE_DATA_EXTENSO = re.compile(
    r"(?<!\d)(\d{1,2})\s+de\s+(" + "|".join(MESES_PT) + r")\s+de\s+(\d{4})(?!\d)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Tipos de prazo (pistas textuais, não decisão final)
# ---------------------------------------------------------------------------
TIPOS_PRAZO: dict[str, tuple[str, ...]] = {
    "Contrarrazões": ("contrarraz", "contra-raz"),
    "Razões Finais": ("razões finais", "razoes finais", "razões finais", "memoriais"),
    "Manifestação": ("manifesta", "manifeste"),
    "Impugnação": ("impugna",),
    "Recurso Ordinário": ("recurso ordinário", "recurso ordinario"),
    "Recurso de Revista": ("recurso de revista",),
    "Agravo de Instrumento": ("agravo de instrumento", "agravo de petição"),
    "Agravo Interno": ("agravo interno", "agravo regimental"),
    "Embargos de Declaração": ("embargos de declara",),
    "Embargos à Execução": ("embargos à execução", "embargos a execucao"),
    "Apelação": ("apelação", "apelacao"),
    "Réplica": ("réplica", "replica"),
    "Alegações Finais": ("alegações finais", "alegacoes finais"),
    "Cumprimento de Sentença": ("cumprimento de sentença", "cumprimento de sentenca"),
}

#: Prazos em dias mencionados no texto ("prazo de 15 dias").
RE_PRAZO_DIAS = re.compile(
    r"prazo\s+de\s+(\d{1,3})\s*(dias?\s*úteis?|dias?|dias?\s*uteis?)", re.IGNORECASE
)


@dataclass(slots=True)
class AnaliseTexto:
    """Resultado da análise determinística de um texto."""

    processos: list[str] = field(default_factory=list)
    datas: list[date] = field(default_factory=list)
    datas_brutas: list[str] = field(default_factory=list)
    tipos_sugeridos: list[str] = field(default_factory=list)
    prazo_dias: int | None = None

    @property
    def processo_principal(self) -> str:
        return self.processos[0] if self.processos else ""

    def as_dict(self) -> dict:
        return {
            "processos": self.processos,
            "datas": [d.isoformat() for d in self.datas],
            "datas_brutas": self.datas_brutas,
            "tipos_sugeridos": self.tipos_sugeridos,
            "prazo_dias": self.prazo_dias,
        }


# ---------------------------------------------------------------------------
def formatar_processo(digitos: tuple[str, ...] | list[str]) -> str:
    """Monta o formato canônico a partir das 6 partes."""
    return (
        f"{digitos[0]}-{digitos[1]}.{digitos[2]}.{digitos[3]}.{digitos[4]}.{digitos[5]}"
    )


def _adicionar(lista: list[str], valor: str) -> None:
    if valor and valor not in lista:
        lista.append(valor)


def extrair_numeros_processo(texto: str | None) -> list[str]:
    """Extrai todos os números de processo CNJ encontrados, sem repetição.

    Aceita o formato canônico e variações com espaços/separadores ausentes.
    """
    if not texto:
        return []

    encontrados: list[str] = []

    for achado in RE_PROCESSO_CNJ.finditer(texto):
        _adicionar(encontrados, achado.group(0))

    # 20 dígitos colados: 01001388920235010074 -> 0100138-89.2023.5.01.0074
    for achado in RE_PROCESSO_SEM_SEPARADOR.finditer(texto):
        digitos = achado.group(1)
        _adicionar(
            encontrados,
            formatar_processo(
                (
                    digitos[0:7],
                    digitos[7:9],
                    digitos[9:13],
                    digitos[13:14],
                    digitos[14:16],
                    digitos[16:20],
                )
            ),
        )

    for achado in RE_PROCESSO_TOLERANTE.finditer(texto):
        candidato = formatar_processo(achado.groups())
        _adicionar(encontrados, candidato)

    return encontrados


def extrair_processo_do_assunto(assunto: str | None) -> str:
    """Número de processo principal do assunto (ou string vazia)."""
    numeros = extrair_numeros_processo(assunto)
    return numeros[0] if numeros else ""


def _data_valida(ano: int, mes: int, dia: int) -> date | None:
    if not (1990 <= ano <= 2100) or not (1 <= mes <= 12) or not (1 <= dia <= 31):
        return None
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def extrair_datas(texto: str | None) -> list[tuple[date, str]]:
    """Datas encontradas no texto, com o trecho original.

    Datas com 2 dígitos de ano são expandidas para 20xx.
    """
    if not texto:
        return []

    achados: list[tuple[date, str]] = []

    for match in RE_DATA_NUMERICA.finditer(texto):
        dia, mes, ano_bruto = int(match.group(1)), int(match.group(2)), match.group(3)
        ano = int(ano_bruto) + 2000 if len(ano_bruto) == 2 else int(ano_bruto)
        data = _data_valida(ano, mes, dia)
        if data:
            achados.append((data, match.group(0)))

    for match in RE_DATA_EXTENSO.finditer(texto):
        dia = int(match.group(1))
        mes = MESES_PT[match.group(2).lower()]
        ano = int(match.group(3))
        data = _data_valida(ano, mes, dia)
        if data:
            achados.append((data, match.group(0)))

    return achados


def sugerir_tipo_prazo(texto: str | None) -> list[str]:
    """Tipos de prazo cujas palavras-chave aparecem no texto."""
    if not texto:
        return []

    minusculo = texto.lower()
    sugestoes: list[str] = []

    for tipo, palavras in TIPOS_PRAZO.items():
        if any(palavra in minusculo for palavra in palavras):
            _adicionar(sugestoes, tipo)

    return sugestoes


def extrair_prazo_em_dias(texto: str | None) -> int | None:
    """Primeiro "prazo de N dias" encontrado."""
    if not texto:
        return None
    achado = RE_PRAZO_DIAS.search(texto)
    return int(achado.group(1)) if achado else None


def analisar(*textos: str | None) -> AnaliseTexto:
    """Aplica todas as regras determinísticas sobre um ou mais textos."""
    juntos = "\n".join(t for t in textos if t)
    datas = extrair_datas(juntos)

    return AnaliseTexto(
        processos=extrair_numeros_processo(juntos),
        datas=[d for d, _ in datas],
        datas_brutas=[bruta for _, bruta in datas],
        tipos_sugeridos=sugerir_tipo_prazo(juntos),
        prazo_dias=extrair_prazo_em_dias(juntos),
    )


def analisar_email(assunto: str | None, corpo: str | None) -> AnaliseTexto:
    """Análise com prioridade para o assunto (onde fica o número do processo)."""
    do_assunto = analisar(assunto)
    do_corpo = analisar(corpo)

    processos = list(do_assunto.processos)
    for numero in do_corpo.processos:
        _adicionar(processos, numero)

    datas = list(do_assunto.datas)
    brutas = list(do_assunto.datas_brutas)
    for data, bruta in zip(do_corpo.datas, do_corpo.datas_brutas, strict=False):
        if data not in datas:
            datas.append(data)
            brutas.append(bruta)

    tipos = list(do_assunto.tipos_sugeridos)
    for tipo in do_corpo.tipos_sugeridos:
        _adicionar(tipos, tipo)

    return AnaliseTexto(
        processos=processos,
        datas=datas,
        datas_brutas=brutas,
        tipos_sugeridos=tipos,
        prazo_dias=do_assunto.prazo_dias or do_corpo.prazo_dias,
    )
