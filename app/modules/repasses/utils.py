"""Utilitários de normalização do módulo de repasses."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

_RE_NAO_ALFA = re.compile(r"[^a-z0-9]+")


def normalizar_chave(valor: object) -> str:
    """Minúsculas, sem acentos e com separadores colapsados em espaço."""
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.casefold().strip()
    return _RE_NAO_ALFA.sub(" ", texto).strip()


def parse_data(valor: object) -> date | None:
    """Interpreta datas em formatos brasileiros, ISO ou serial do Sheets."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    texto = str(valor or "").strip()
    if not texto:
        return None

    # Serial do Google Sheets (dias desde 1899-12-30).
    if re.fullmatch(r"\d{4,6}(\.0+)?", texto):
        return date(1899, 12, 30) + timedelta(days=int(float(texto)))

    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def formatar_data(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else ""


def parse_valor(valor: object) -> Decimal | None:
    """Interpreta valores monetários com ``R$``, ponto e vírgula."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))

    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return None

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def formatar_valor(valor: Decimal | None) -> str:
    """Formata como ``1.234,56`` (pt-BR)."""
    if valor is None:
        return ""
    texto = f"{valor:,.2f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def agora_local(fuso: str) -> datetime:
    """Agora no fuso informado, sem tzinfo."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(fuso)).replace(tzinfo=None)
