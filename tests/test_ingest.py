"""Testes da orquestração: ingestão em duas etapas, idempotência e estados."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.models import Email, Prazo, StatusExtracao
from app.services.deepseek import DeepSeekError, RespostaInvalida
from app.services.ingest import sincronizar
from tests.conftest import FakeDeepSeek, FakeGmail, mensagem, prazo_ok, settings_teste

QUERY = "in:inbox is:unread"


def executar(db, mensagens, respostas, **kwargs):
    gmail = FakeGmail(mensagens)
    deepseek = FakeDeepSeek(respostas)
    resultado = sincronizar(
        db,
        settings=settings_teste(**kwargs.pop("settings", {})),
        gmail=gmail,
        deepseek=deepseek,
        query=QUERY,
        **kwargs,
    )
    return resultado, gmail, deepseek


def contar(db, modelo) -> int:
    return db.scalar(select(func.count()).select_from(modelo)) or 0


class TestFluxoBasico:
    def test_email_com_prazo(self, db):
        resultado, gmail, _ = executar(
            db,
            [mensagem()],
            {"Intimação": [prazo_ok()]},
        )

        assert resultado.novos == 1
        assert resultado.processados == 1
        assert resultado.prazos_criados == 1
        assert resultado.erros == 0

        email = db.scalars(select(Email)).one()
        assert email.gmail_message_id == "MSG1"
        assert email.status == StatusExtracao.OK.value
        assert email.remetente_nome == "Secretaria TJSP"
        assert email.remetente_email == "protocolo@tjsp.jus.br"
        assert email.tentativas == 1
        assert email.processado_em is not None

        prazo = db.scalars(select(Prazo)).one()
        assert prazo.numero_processo == "0001234-56.2024.8.26.0100"
        assert prazo.email_id == email.id

        assert gmail.lidas == ["MSG1"]

    def test_email_sem_prazo(self, db):
        resultado, gmail, _ = executar(db, [mensagem(assunto="Informativo")], {})

        email = db.scalars(select(Email)).one()
        assert email.status == StatusExtracao.SEM_PRAZO.value
        assert resultado.processados == 1
        assert contar(db, Prazo) == 0
        assert gmail.lidas == ["MSG1"]

    def test_multiplos_prazos_geram_uma_linha_por_prazo(self, db):
        prazos = [
            prazo_ok(tipo="Recurso de Revista", processo="0002222-33.2024.5.02.0001"),
            prazo_ok(tipo="Contrarrazões", processo="0003333-44.2024.5.02.0002"),
        ]
        resultado, _, _ = executar(db, [mensagem(assunto="Dois prazos")], {"Dois prazos": prazos})

        assert resultado.prazos_criados == 2
        assert contar(db, Email) == 1
        assert contar(db, Prazo) == 2
        assert db.scalars(select(Email)).one().status == StatusExtracao.OK.value

    def test_prazo_incompleto_marca_revisar(self, db):
        from app.services.normalizacao import PrazoExtraido

        incompleto = PrazoExtraido(
            advogado="Não identificado",
            tipo_prazo="Manifestação",
            data_prazo=None,
            numero_processo="",
            status=StatusExtracao.REVISAR.value,
        )
        executar(db, [mensagem()], {"Intimação": [incompleto]})

        assert db.scalars(select(Email)).one().status == StatusExtracao.REVISAR.value


class TestIdempotencia:
    def test_nao_reprocessa_status_final(self, db):
        resultado1, _, deepseek1 = executar(db, [mensagem()], {"Intimação": [prazo_ok()]})
        resultado2, gmail2, deepseek2 = executar(db, [mensagem()], {"Intimação": [prazo_ok()]})

        assert resultado1.processados == 1
        assert resultado2.ignorados == 1
        assert resultado2.processados == 0
        assert len(deepseek1.chamadas) == 1
        assert len(deepseek2.chamadas) == 0

        assert contar(db, Email) == 1
        assert contar(db, Prazo) == 1

    def test_reprocessa_erro_e_conclui(self, db):
        executar(db, [mensagem()], {"Intimação": DeepSeekError("HTTP 500")})

        email = db.scalars(select(Email)).one()
        assert email.status == StatusExtracao.ERRO_IA.value
        assert email.erro
        assert email.tentativas == 1

        resultado, gmail, _ = executar(db, [mensagem()], {"Intimação": [prazo_ok()]})

        db.expire_all()
        email = db.scalars(select(Email)).one()
        assert resultado.processados == 1
        assert resultado.ignorados == 0
        assert email.status == StatusExtracao.OK.value
        assert email.erro is None
        assert email.tentativas == 2
        assert contar(db, Prazo) == 1

    def test_nao_marca_como_lida_em_erro(self, db):
        _, gmail, _ = executar(db, [mensagem()], {"Intimação": RespostaInvalida("lixo")})
        assert gmail.lidas == []


class TestEstadosDeErro:
    def test_json_invalido(self, db):
        executar(db, [mensagem()], {"Intimação": RespostaInvalida("não é json")})
        email = db.scalars(select(Email)).one()
        assert email.status == StatusExtracao.ERRO_JSON.value

    def test_preserva_dados_de_ingestao_no_erro(self, db):
        executar(db, [mensagem()], {"Intimação": DeepSeekError("timeout")})
        email = db.scalars(select(Email)).one()

        assert email.remetente_nome == "Secretaria TJSP"
        assert email.remetente_email == "protocolo@tjsp.jus.br"
        assert email.assunto == "Intimação"
        assert email.data_recebimento is not None

    def test_falha_ao_baixar_mensagem(self, db):
        gmail = FakeGmail([mensagem()])
        gmail.falhar_em.add("MSG1")
        resultado = sincronizar(
            db,
            settings=settings_teste(),
            gmail=gmail,
            deepseek=FakeDeepSeek({}),
            query=QUERY,
        )

        assert resultado.erros == 1
        assert resultado.falhas
        assert contar(db, Email) == 0

    def test_reprocessamento_substitui_prazos(self, db):
        executar(
            db,
            [mensagem()],
            {"Intimação": DeepSeekError("500")},
        )
        executar(db, [mensagem()], {"Intimação": [prazo_ok(), prazo_ok(tipo="Embargos")]})

        assert contar(db, Prazo) == 2


class TestConfiguracao:
    def test_nao_marca_lida_quando_desabilitado(self, db):
        _, gmail, _ = executar(
            db,
            [mensagem()],
            {"Intimação": [prazo_ok()]},
            settings={"gmail_marcar_como_lida": False},
        )
        assert gmail.lidas == []


class TestNumeroProcessoDoAssunto:
    ASSUNTO = "Intimação para manifestação - Processo: 0100138-89.2023.5.01.0074"

    def test_grava_processo_do_assunto_na_ingestao(self, db):
        resultado, _, deepseek = executar(
            db,
            [mensagem(assunto=self.ASSUNTO)],
            {self.ASSUNTO: []},
        )

        email = db.scalars(select(Email)).one()
        assert email.numero_processo == "0100138-89.2023.5.01.0074"
        assert resultado.prazos_criados == 1  # linha de rastreamento

    def test_envia_processo_como_dica_para_a_ia(self, db):
        _, _, deepseek = executar(db, [mensagem(assunto=self.ASSUNTO)], {self.ASSUNTO: []})
        assert deepseek.processos_recebidos == [["0100138-89.2023.5.01.0074"]]

    def test_preenche_processo_que_a_ia_nao_encontrou(self, db):
        from app.services.normalizacao import PrazoExtraido

        sem_processo = PrazoExtraido(
            advogado="Dra. Maria Souza",
            tipo_prazo="Manifestação",
            data_prazo=date(2026, 11, 20),
            numero_processo="",
            status=StatusExtracao.REVISAR.value,
        )
        executar(db, [mensagem(assunto=self.ASSUNTO)], {self.ASSUNTO: [sem_processo]})

        prazo = db.scalars(select(Prazo)).one()
        assert prazo.numero_processo == "0100138-89.2023.5.01.0074"
        assert prazo.status == StatusExtracao.OK.value

    def test_cria_linha_de_rastreamento_sem_prazo(self, db):
        from app.services.normalizacao import TIPO_NAO_IDENTIFICADO

        executar(db, [mensagem(assunto=self.ASSUNTO)], {self.ASSUNTO: []})

        email = db.scalars(select(Email)).one()
        prazo = db.scalars(select(Prazo)).one()

        assert email.status == StatusExtracao.REVISAR.value
        assert prazo.numero_processo == "0100138-89.2023.5.01.0074"
        assert prazo.tipo_prazo == TIPO_NAO_IDENTIFICADO
        assert prazo.data_prazo is None

    def test_varios_processos_no_assunto_gera_uma_linha_por_processo(self, db):
        assunto = (
            "Prazos - 0002222-33.2024.5.02.0001 e 0003333-44.2024.5.02.0002"
        )
        executar(db, [mensagem(assunto=assunto)], {assunto: []})

        prazos = db.scalars(select(Prazo)).all()
        assert sorted(p.numero_processo for p in prazos) == [
            "0002222-33.2024.5.02.0001",
            "0003333-44.2024.5.02.0002",
        ]

    def test_nao_sobrescreve_processo_declarado_pela_ia(self, db):
        # assunto com 1 processo, mas a IA identificou outro (cenário raro)
        executar(
            db,
            [mensagem(assunto=self.ASSUNTO)],
            {self.ASSUNTO: [prazo_ok(processo="0009999-11.2024.8.26.0100")]},
        )

        prazo = db.scalars(select(Prazo)).one()
        assert prazo.numero_processo == "0009999-11.2024.8.26.0100"


class TestEspelhamento:
    def test_sem_planilha_configurada_nao_falha(self, db):
        resultado, _, _ = executar(db, [mensagem()], {"Intimação": [prazo_ok()]})
        assert resultado.planilha is None

    def test_resumo_inclui_chave_planilha(self, db):
        resultado, _, _ = executar(db, [mensagem()], {"Intimação": [prazo_ok()]})
        assert "planilha" in resultado.as_dict()
