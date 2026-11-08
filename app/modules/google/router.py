"""Camada de integração Google: páginas e API.

Expõe operações genéricas sobre Gmail, Sheets e Docs. Regras de negócio
(extração de prazos, repasses, ...) ficam nos módulos consumidores.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.deps import exigir_token, settings_dep
from app.core.templating import contexto_base, templates
from app.modules.google.docs import DocsClient, DocsError
from app.modules.google.gmail import GmailClient, GmailError, MensagemEmail
from app.modules.google.sheets import SheetsClient, SheetsError

router = APIRouter(tags=["google"])


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------
class ValoresSheets(BaseModel):
    aba: str | None = None
    range: str = Field(default="A1:ZZ", alias="range")
    valores: list[list[str]]


class NovaPlanilha(BaseModel):
    titulo: str


class NovoDocumento(BaseModel):
    titulo: str


class TextoDocumento(BaseModel):
    texto: str


class SubstituicaoDocumento(BaseModel):
    de: str
    para: str
    diferenciar_maiusculas: bool = True


class MensagemEnvio(BaseModel):
    para: str
    assunto: str
    corpo: str
    cc: str | None = None
    bcc: str | None = None
    html: bool = False


# ---------------------------------------------------------------------------
# Serialização
# ---------------------------------------------------------------------------
def _mensagem_dict(mensagem: MensagemEmail) -> dict:
    return {
        "id": mensagem.id,
        "thread_id": mensagem.thread_id,
        "remetente_nome": mensagem.remetente_nome,
        "remetente_email": mensagem.remetente_email,
        "assunto": mensagem.assunto,
        "data_recebimento": mensagem.data_recebimento.isoformat(),
        "corpo": mensagem.corpo,
    }


# ---------------------------------------------------------------------------
# Página de status
# ---------------------------------------------------------------------------
@router.get("/google", summary="Estado da integração Google")
def pagina_google(request: Request, settings: Settings = Depends(settings_dep)):
    return templates.TemplateResponse(
        request=request,
        name="status.html",
        context=contexto_base(
            titulo="Integração Google",
            rota_ativa="google",
            google_configurado=settings.google_configurado,
            gmail_configurado=settings.gmail_configurado,
            sheets_configurado=settings.sheets_configurado,
            repasses_configurado=settings.repasses_configurado,
        ),
    )


@router.get("/api/google/status", summary="Estado da integração Google em JSON")
def status_google(settings: Settings = Depends(settings_dep)) -> dict:
    return {
        "conta_servico_configurada": settings.google_configurado,
        "gmail_configurado": settings.gmail_configurado,
        "sheets_prazos_configurado": settings.sheets_configurado,
        "repasses_configurado": settings.repasses_configurado,
        "docs_disponivel": settings.google_configurado,
        "rotas": {
            "gmail": "/api/google/gmail/mensagens",
            "sheets": "/api/google/sheets/{sheet_id}/valores",
            "docs": "/api/google/docs/{document_id}",
        },
    }


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------
@router.get(
    "/api/google/gmail/mensagens",
    summary="Lista mensagens do Gmail",
    dependencies=[Depends(exigir_token)],
)
def listar_mensagens(
    q: str = Query(default="in:inbox", description="Query do Gmail"),
    limite: int = Query(default=25, ge=1, le=100),
    pagina_token: str | None = None,
    detalhar: bool = Query(
        default=False, description="Baixa o conteúdo completo de cada mensagem"
    ),
    settings: Settings = Depends(settings_dep),
) -> dict:
    if not settings.gmail_configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credenciais do Gmail não configuradas.",
        )

    cliente = GmailClient(settings)
    try:
        pagina = cliente.listar_mensagens(
            q, max_results=limite, page_token=pagina_token
        )
        mensagens = (
            [_mensagem_dict(cliente.obter_mensagem(identificador)) for identificador in pagina.ids]
            if detalhar
            else []
        )
    except GmailError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {
        "ids": pagina.ids,
        "proximo_page_token": pagina.proximo_page_token,
        "mensagens": mensagens,
    }


@router.post(
    "/api/google/gmail/mensagens",
    summary="Envia uma mensagem pelo Gmail",
    dependencies=[Depends(exigir_token)],
)
def enviar_mensagem(
    payload: MensagemEnvio, settings: Settings = Depends(settings_dep)
) -> dict:
    if not settings.gmail_configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credenciais do Gmail não configuradas.",
        )

    try:
        return GmailClient(settings).enviar_mensagem(
            para=payload.para,
            assunto=payload.assunto,
            corpo=payload.corpo,
            cc=payload.cc,
            bcc=payload.bcc,
            html=payload.html,
        )
    except GmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc


@router.get(
    "/api/google/gmail/mensagens/{message_id}",
    summary="Obtém uma mensagem do Gmail",
    dependencies=[Depends(exigir_token)],
)
def obter_mensagem(
    message_id: str, settings: Settings = Depends(settings_dep)
) -> dict:
    if not settings.gmail_configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credenciais do Gmail não configuradas.",
        )

    try:
        return _mensagem_dict(GmailClient(settings).obter_mensagem(message_id))
    except GmailError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/api/google/gmail/mensagens/{message_id}/lida",
    summary="Marca uma mensagem como lida",
    dependencies=[Depends(exigir_token)],
)
def marcar_como_lida(
    message_id: str, settings: Settings = Depends(settings_dep)
) -> dict:
    if not settings.gmail_configurado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credenciais do Gmail não configuradas.",
        )

    try:
        ok = GmailClient(settings).marcar_como_lida(message_id)
    except GmailError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {"ok": ok, "message_id": message_id}


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
def _cliente_sheets(sheet_id: str, settings: Settings, aba: str | None) -> SheetsClient:
    return SheetsClient(settings, sheet_id=sheet_id, aba=aba)


@router.get(
    "/api/google/sheets/{sheet_id}/metadados",
    summary="Metadados de uma planilha",
    dependencies=[Depends(exigir_token)],
)
def metadados_planilha(
    sheet_id: str, settings: Settings = Depends(settings_dep)
) -> dict:
    try:
        return _cliente_sheets(sheet_id, settings, None).obter_metadados()
    except SheetsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get(
    "/api/google/sheets/{sheet_id}/valores",
    summary="Lê valores de uma planilha",
    dependencies=[Depends(exigir_token)],
)
def ler_valores_planilha(
    sheet_id: str,
    aba: str = Query(default="Página1"),
    range: str = Query(default="A1:ZZ", alias="range"),
    settings: Settings = Depends(settings_dep),
) -> dict:
    try:
        valores = _cliente_sheets(sheet_id, settings, aba).ler_valores(range)
    except SheetsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {"sheet_id": sheet_id, "aba": aba, "range": range, "valores": valores}


@router.put(
    "/api/google/sheets/{sheet_id}/valores",
    summary="Escreve valores em uma planilha",
    dependencies=[Depends(exigir_token)],
)
def escrever_valores_planilha(
    sheet_id: str,
    payload: ValoresSheets,
    settings: Settings = Depends(settings_dep),
) -> dict:
    try:
        _cliente_sheets(sheet_id, settings, payload.aba).escrever_valores(
            payload.range, payload.valores
        )
    except SheetsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {"ok": True}


@router.post(
    "/api/google/sheets/{sheet_id}/append",
    summary="Anexa valores ao final de uma planilha",
    dependencies=[Depends(exigir_token)],
)
def anexar_valores_planilha(
    sheet_id: str,
    payload: ValoresSheets,
    settings: Settings = Depends(settings_dep),
) -> dict:
    try:
        intervalo = _cliente_sheets(sheet_id, settings, payload.aba).anexar(
            payload.valores, referencia=payload.range
        )
    except SheetsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {"ok": True, "intervalo_atualizado": intervalo}


@router.post(
    "/api/google/sheets",
    summary="Cria uma nova planilha",
    dependencies=[Depends(exigir_token)],
)
def criar_planilha(
    payload: NovaPlanilha, settings: Settings = Depends(settings_dep)
) -> dict:
    try:
        return SheetsClient(settings).criar_planilha(payload.titulo)
    except SheetsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------
@router.get(
    "/api/google/docs/{document_id}",
    summary="Lê o texto de um documento",
    dependencies=[Depends(exigir_token)],
)
def ler_documento(
    document_id: str,
    completo: bool = Query(default=False, description="Inclui a estrutura completa"),
    settings: Settings = Depends(settings_dep),
) -> dict:
    cliente = DocsClient(settings)
    try:
        documento = cliente.obter_documento(document_id)
    except DocsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    from app.modules.google.docs import extrair_texto

    resposta = {
        "document_id": document_id,
        "titulo": documento.get("title", ""),
        "texto": extrair_texto(documento),
    }
    if completo:
        resposta["documento"] = documento
    return resposta


@router.post(
    "/api/google/docs",
    summary="Cria um documento",
    dependencies=[Depends(exigir_token)],
)
def criar_documento(
    payload: NovoDocumento, settings: Settings = Depends(settings_dep)
) -> dict:
    try:
        return DocsClient(settings).criar_documento(payload.titulo)
    except DocsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/api/google/docs/{document_id}/texto",
    summary="Anexa texto ao final de um documento",
    dependencies=[Depends(exigir_token)],
)
def anexar_texto_documento(
    document_id: str,
    payload: TextoDocumento,
    settings: Settings = Depends(settings_dep),
) -> dict:
    try:
        return DocsClient(settings).anexar_texto(document_id, payload.texto)
    except DocsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/api/google/docs/{document_id}/substituir",
    summary="Substitui texto em um documento",
    dependencies=[Depends(exigir_token)],
)
def substituir_texto_documento(
    document_id: str,
    payload: SubstituicaoDocumento,
    settings: Settings = Depends(settings_dep),
) -> dict:
    try:
        return DocsClient(settings).substituir_texto(
            document_id,
            payload.de,
            payload.para,
            diferenciar_maiusculas=payload.diferenciar_maiusculas,
        )
    except DocsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
