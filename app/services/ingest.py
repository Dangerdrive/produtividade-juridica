"""Orquestração da ingestão.

Fluxo por mensagem (o mesmo princípio da Fase 1 original):
1. **Ingestão determinística** — grava remetente, data de recebimento, assunto e
   o número de processo extraído do assunto por regex. Isso acontece *antes* de
   qualquer chamada à IA.
2. **Enriquecimento** — envia o corpo ao modelo e grava os prazos.
3. **Estado final** — marca como lida (opcional) somente em caso de sucesso.
4. **Espelhamento** — replica os prazos na planilha do Google Sheets.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    STATUS_FINAIS,
    Email,
    Prazo,
    StatusExtracao,
    SyncState,
    utcnow,
)
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.extracao import extrair_numeros_processo
from app.services.gmail import GmailClient, GmailError
from app.services.normalizacao import (
    ADVOGADO_NAO_IDENTIFICADO,
    TIPO_NAO_IDENTIFICADO,
    PrazoExtraido,
)
from app.services.sheets import ResultadoPlanilha, espelhar_apos_ingestao

CHAVE_CHECKPOINT = "gmail_checkpoint"


@dataclass(slots=True)
class ResultadoSincronizacao:
    """Resumo de uma execução."""

    query: str = ""
    novos: int = 0
    processados: int = 0
    ignorados: int = 0
    erros: int = 0
    prazos_criados: int = 0
    interrompido: bool = False
    duracao_s: float = 0.0
    planilha: ResultadoPlanilha | None = None
    falhas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "novos": self.novos,
            "processados": self.processados,
            "ignorados": self.ignorados,
            "erros": self.erros,
            "prazos_criados": self.prazos_criados,
            "interrompido": self.interrompido,
            "duracao_s": self.duracao_s,
            "planilha": self.planilha.as_dict() if self.planilha else None,
            "falhas": self.falhas[:10],
            "avisos": self.avisos[:10],
        }


# ---------------------------------------------------------------------------
# Queries do Gmail
# ---------------------------------------------------------------------------
def query_continuo(settings: Settings) -> str:
    """E-mails não lidos da caixa de entrada."""
    return f"{settings.gmail_query} is:unread"


def query_historico(settings: Settings, dias: int | None = None) -> str:
    """Janela retroativa (padrão: ``GMAIL_LOOKBACK_DAYS``)."""
    dias = settings.gmail_lookback_days if dias is None else dias
    inicio = datetime.now(UTC).date() - timedelta(days=dias)
    return f"{settings.gmail_query} after:{inicio:%Y/%m/%d}"


# ---------------------------------------------------------------------------
# Checkpoint de paginação
# ---------------------------------------------------------------------------
def _ler_checkpoint(db: Session) -> dict | None:
    registro = db.get(SyncState, CHAVE_CHECKPOINT)
    if not registro or not registro.valor:
        return None
    try:
        return json.loads(registro.valor)
    except json.JSONDecodeError:
        return None


def _salvar_checkpoint(db: Session, query: str, page_token: str | None) -> None:
    registro = db.get(SyncState, CHAVE_CHECKPOINT)

    if not page_token:
        if registro is not None:
            db.delete(registro)
            db.commit()
        return

    valor = json.dumps({"query": query, "page_token": page_token})
    if registro is None:
        db.add(SyncState(chave=CHAVE_CHECKPOINT, valor=valor))
    else:
        registro.valor = valor
    db.commit()


# ---------------------------------------------------------------------------
# Sincronização
# ---------------------------------------------------------------------------
def sincronizar(
    db: Session,
    *,
    settings: Settings,
    gmail: GmailClient,
    deepseek: DeepSeekClient,
    query: str | None = None,
    limite: int | None = None,
    usar_checkpoint: bool = False,
    deadline_s: float | None = None,
) -> ResultadoSincronizacao:
    """Processa mensagens do Gmail respeitando o orçamento de tempo do serverless."""
    query = query or settings.gmail_query
    limite = settings.gmail_max_mensagens_por_execucao if limite is None else limite
    deadline_s = settings.max_segundos_por_execucao if deadline_s is None else deadline_s

    inicio = time.monotonic()
    limite_tempo = inicio + deadline_s

    resultado = ResultadoSincronizacao(query=query)

    page_token: str | None = None
    if usar_checkpoint:
        checkpoint = _ler_checkpoint(db)
        if checkpoint and checkpoint.get("query") == query:
            page_token = checkpoint.get("page_token")

    tentadas = 0

    while True:
        if time.monotonic() >= limite_tempo or tentadas >= limite:
            resultado.interrompido = True
            break

        pagina = gmail.listar_mensagens(
            query,
            max_results=min(50, max(limite - tentadas, 1)),
            page_token=page_token,
        )

        if not pagina.ids:
            page_token = None
            break

        for message_id in pagina.ids:
            if time.monotonic() >= limite_tempo:
                resultado.interrompido = True
                break

            sincronizar_mensagem(
                db,
                settings=settings,
                gmail=gmail,
                deepseek=deepseek,
                message_id=message_id,
                resultado=resultado,
            )

            tentadas += 1
            if tentadas >= limite:
                resultado.interrompido = pagina.proximo_page_token is not None
                break

        page_token = pagina.proximo_page_token

        if resultado.interrompido or not page_token:
            break

    if usar_checkpoint:
        _salvar_checkpoint(db, query, page_token if resultado.interrompido else None)

    # Espelha o resultado na planilha do Google Sheets (dashboard em Apps Script).
    resultado.planilha = espelhar_apos_ingestao(db, settings)

    resultado.duracao_s = round(time.monotonic() - inicio, 2)
    return resultado


def sincronizar_mensagem(
    db: Session,
    *,
    settings: Settings,
    gmail: GmailClient,
    deepseek: DeepSeekClient,
    message_id: str,
    resultado: ResultadoSincronizacao,
) -> None:
    """Processa uma mensagem (idempotente por ``gmail_message_id``)."""
    existente = db.scalar(select(Email).where(Email.gmail_message_id == message_id))

    if existente is not None and existente.status in STATUS_FINAIS:
        resultado.ignorados += 1
        _marcar_como_lida(gmail, settings, message_id, resultado)
        return

    try:
        mensagem = gmail.obter_mensagem(message_id)
    except GmailError as exc:
        resultado.erros += 1
        resultado.falhas.append(f"{message_id}: {exc}")
        return

    # --- Etapa 1: ingestão determinística (sem IA) --------------------
    processos = extrair_numeros_processo(mensagem.assunto)
    if not processos:
        # Sem número no assunto, tenta o início do corpo (melhor esforço).
        processos = extrair_numeros_processo(mensagem.corpo[:2000])

    email = existente
    if email is None:
        email = Email(
            gmail_message_id=mensagem.id,
            gmail_thread_id=mensagem.thread_id,
            remetente_nome=mensagem.remetente_nome,
            remetente_email=mensagem.remetente_email,
            assunto=mensagem.assunto,
            data_recebimento=mensagem.data_recebimento,
            numero_processo=processos[0] if processos else "",
            status=StatusExtracao.PENDENTE_IA.value,
            tentativas=0,
            criado_em=utcnow(),
        )
        db.add(email)
        resultado.novos += 1
    else:
        email.remetente_nome = mensagem.remetente_nome
        email.remetente_email = mensagem.remetente_email
        email.assunto = mensagem.assunto
        email.data_recebimento = mensagem.data_recebimento
        email.numero_processo = processos[0] if processos else ""

    email.tentativas = (email.tentativas or 0) + 1
    db.commit()  # durabilidade antes da chamada externa

    # --- Etapa 2: enriquecimento por IA -------------------------------
    try:
        prazos = deepseek.extrair_prazos(
            assunto=mensagem.assunto,
            remetente_nome=mensagem.remetente_nome,
            remetente_email=mensagem.remetente_email,
            data_recebimento=mensagem.data_recebimento.strftime("%d/%m/%Y %H:%M"),
            corpo=mensagem.corpo,
            processos_conhecidos=processos,
        )
    except DeepSeekError as exc:
        email.status = exc.status
        email.erro = str(exc)[:1000]
        email.processado_em = utcnow()
        db.commit()

        resultado.erros += 1
        resultado.falhas.append(f"{message_id}: {exc}")
        return

    prazos = _completar_com_assunto(prazos, processos)

    email.prazos.clear()
    for prazo in prazos:
        email.prazos.append(
            Prazo(
                advogado=prazo.advogado,
                tipo_prazo=prazo.tipo_prazo,
                data_prazo=prazo.data_prazo,
                numero_processo=prazo.numero_processo,
                status=prazo.status,
                atualizado_em=utcnow(),
            )
        )

    if not prazos:
        email.status = StatusExtracao.SEM_PRAZO.value
    elif any(p.status != StatusExtracao.OK.value for p in prazos):
        email.status = StatusExtracao.REVISAR.value
    else:
        email.status = StatusExtracao.OK.value

    email.erro = None
    email.processado_em = utcnow()
    db.commit()

    resultado.processados += 1
    resultado.prazos_criados += len(prazos)

    _marcar_como_lida(gmail, settings, message_id, resultado)


def _marcar_como_lida(
    gmail: GmailClient,
    settings: Settings,
    message_id: str,
    resultado: ResultadoSincronizacao,
) -> None:
    if not settings.gmail_marcar_como_lida:
        return
    try:
        gmail.marcar_como_lida(message_id)
    except GmailError as exc:
        resultado.avisos.append(f"{message_id}: {exc}")


def _completar_com_assunto(
    prazos: list[PrazoExtraido], processos: list[str]
) -> list[PrazoExtraido]:
    """Usa o número de processo do assunto para preencher lacunas da IA.

    Só preenche quando o assunto traz **exatamente um** processo — caso
    contrário haveria risco de atribuir o processo errado ao prazo.
    """
    if not processos:
        return prazos

    if prazos:
        if len(processos) == 1:
            for prazo in prazos:
                if not prazo.numero_processo:
                    prazo.numero_processo = processos[0]
                    prazo.status = (
                        StatusExtracao.OK.value
                        if prazo.completo
                        else StatusExtracao.REVISAR.value
                    )
        return prazos

    # Nenhum prazo identificado pela IA: registra o(s) processo(s) do assunto
    # como linha de rastreamento, para revisão manual.
    return [
        PrazoExtraido(
            advogado=ADVOGADO_NAO_IDENTIFICADO,
            tipo_prazo=TIPO_NAO_IDENTIFICADO,
            data_prazo=None,
            numero_processo=numero,
            status=StatusExtracao.REVISAR.value,
        )
        for numero in processos
    ]
