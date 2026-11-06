/**
 * Main.gs
 * -----------------------------------------------------------------------------
 * Orquestração do projeto.
 *
 * Funções públicas (executáveis pelo menu / gatilho):
 *   setupInicial()             -> prepara abas, cabeçalhos e dashboard
 *   processarEmailsProtocolo() -> rotina contínua (não lidos)
 *   processarHistorico2Meses() -> carga retroativa de 60 dias (retomável)
 *   atualizarDashboard()       -> reconstrói a tabela dinâmica
 *   instalarGatilho()          -> gatilho de tempo de 15 em 15 minutos
 *   testarConexaoDeepSeek()    -> smoke test da API
 */

/* ========================================================================== *
 * Funções públicas
 * ========================================================================== */

/**
 * Rotina contínua: processa SOMENTE mensagens não lidas da caixa de entrada.
 * @return {Object} Resumo da execução.
 */
function processarEmailsProtocolo() {
  return processarLote_(CONFIG.EMAIL.QUERY_CONTINUO, {
    marcarComoLida: true,
    offsetInicial: 0,
    tamanhoPagina: CONFIG.EMAIL.TAMANHO_PAGINA_CONTINUO
  });
}

/**
 * Carga inicial: busca retroativa dos últimos 60 dias.
 * A data-âncora e o offset ficam salvos em Script Properties, de modo que
 * execuções consecutivas continuam de onde pararam.
 * @return {Object} Resumo da execução.
 */
function processarHistorico2Meses() {
  const props = PropertiesService.getScriptProperties();
  let ancora = props.getProperty(CONFIG.PROPS.ANCORA_HISTORICO);

  if (!ancora) {
    const referencia = new Date();
    referencia.setDate(referencia.getDate() - CONFIG.EMAIL.DIAS_HISTORICO);
    ancora = Utilities.formatDate(referencia, CONFIG.FUSO, 'yyyy/MM/dd');
    props.setProperty(CONFIG.PROPS.ANCORA_HISTORICO, ancora);
  }

  const offsetSalvo = Number(props.getProperty(CONFIG.PROPS.OFFSET_HISTORICO) || 0) || 0;

  const resumo = processarLote_(
    CONFIG.EMAIL.QUERY_BASE + ' after:' + ancora,
    {
      marcarComoLida: false,
      offsetInicial: offsetSalvo,
      tamanhoPagina: CONFIG.EMAIL.TAMANHO_PAGINA_HISTORICO
    }
  );

  if (resumo.tempoEsgotado) {
    props.setProperty(CONFIG.PROPS.OFFSET_HISTORICO, String(resumo.proximoOffset));
    console.log('Carga histórica incompleta (limite de tempo). ' +
      'Execute novamente para continuar do offset ' + resumo.proximoOffset + '.');
  } else {
    props.deleteProperty(CONFIG.PROPS.OFFSET_HISTORICO);
    props.deleteProperty(CONFIG.PROPS.ANCORA_HISTORICO);
    console.log('Carga histórica concluída.');
  }

  return resumo;
}

/** Wrapper público para o menu. */
function atualizarDashboard() {
  atualizarDashboard_();
}

/**
 * Prepara a planilha: abas, cabeçalhos, formatos e dashboard.
 */
function setupInicial() {
  const planilha = obterPlanilha_();
  const props = PropertiesService.getScriptProperties();

  const abaDB = obterAbaDB_();
  aplicarFormatosColunas_(abaDB);
  atualizarDashboard_();

  if (!props.getProperty(CONFIG.PROPS.DEEPSEEK_API_KEY)) {
    console.warn('Configure a propriedade de script ' +
      CONFIG.PROPS.DEEPSEEK_API_KEY + ' antes de processar e-mails.');
  }

  console.log('Setup concluído na planilha ' + planilha.getName() +
    '. Abas: ' + planilha.getSheets()
      .map(function (aba) { return aba.getName(); }).join(', '));
}

/**
 * Instala (ou substitui) o gatilho de tempo da rotina contínua.
 */
function instalarGatilho() {
  const existentes = ScriptApp.getProjectTriggers();

  for (let i = 0; i < existentes.length; i++) {
    if (existentes[i].getHandlerFunction() === 'processarEmailsProtocolo') {
      ScriptApp.deleteTrigger(existentes[i]);
    }
  }

  ScriptApp.newTrigger('processarEmailsProtocolo')
    .timeBased()
    .everyMinutes(15)
    .create();

  console.log('Gatilho instalado: processarEmailsProtocolo() a cada 15 minutos.');
}

/**
 * Smoke test: envia um e-mail fictício ao modelo e imprime o resultado.
 * @return {Object[]}
 */
function testarConexaoDeepSeek() {
  const base = {
    messageId: 'TESTE',
    threadId: 'TESTE',
    dataRecebimento: new Date(),
    remetenteNome: 'Secretaria de Protocolo',
    remetenteEmail: 'protocolo@exemplo.com.br',
    assunto: 'Intimação para manifestação',
    corpo: 'Fica a Dra. Maria Souza intimada a se manifestar, no prazo de 15 dias, ' +
      'nos autos do processo 0001234-56.2024.8.26.0100.'
  };

  const prazos = extrairPrazosDoEmail_(base);
  console.log(JSON.stringify(prazos, null, 2));
  return prazos;
}

/** Menu na planilha. */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('⚖️ Produtividade Jurídica')
    .addItem('1. Configuração inicial', 'setupInicial')
    .addSeparator()
    .addItem('Processar e-mails não lidos', 'processarEmailsProtocolo')
    .addItem('Importar histórico (60 dias)', 'processarHistorico2Meses')
    .addSeparator()
    .addItem('Atualizar Dashboard', 'atualizarDashboard')
    .addItem('Testar conexão com a IA', 'testarConexaoDeepSeek')
    .addSeparator()
    .addItem('Instalar gatilho (15 min)', 'instalarGatilho')
    .addToUi();
}

/* ========================================================================== *
 * Núcleo
 * ========================================================================== */

/**
 * Percorre a busca em páginas e processa cada mensagem.
 * @param {string} query
 * @param {Object} opcoes { marcarComoLida, offsetInicial, tamanhoPagina }
 * @return {Object} Resumo
 */
function processarLote_(query, opcoes) {
  const inicio = Date.now();
  const aba = obterAbaDB_();
  const indice = obterIndiceMensagens_(aba);

  const resumo = {
    query: query,
    paginas: 0,
    encontrados: 0,
    processados: 0,
    ignorados: 0,
    erros: 0,
    linhasIngestao: 0,
    linhasExtras: 0,
    tempoEsgotado: false,
    proximoOffset: 0
  };

  let offset = opcoes.offsetInicial || 0;

  while (true) {
    const offsetPagina = offset;
    const threads = buscarThreads_(query, offset, opcoes.tamanhoPagina);

    if (!threads.length) {
      resumo.proximoOffset = 0;
      break;
    }

    resumo.paginas++;
    const mensagens = mensagensDasThreads_(threads);
    resumo.encontrados += mensagens.length;

    let interrompido = false;

    for (let i = 0; i < mensagens.length; i++) {
      if (Date.now() - inicio > CONFIG.EMAIL.TEMPO_MAXIMO_EXECUCAO_MS) {
        interrompido = true;
        break;
      }

      processarMensagem_(aba, indice, mensagens[i], opcoes, resumo);
    }

    if (interrompido) {
      // Retoma do início desta página: reprocessar é seguro (idempotente).
      resumo.tempoEsgotado = true;
      resumo.proximoOffset = offsetPagina;
      break;
    }

    offset += threads.length;

    if (threads.length < opcoes.tamanhoPagina) {
      resumo.proximoOffset = 0;
      break;
    }
  }

  if (resumo.linhasIngestao > 0 || resumo.linhasExtras > 0) {
    try {
      atualizarDashboard_();
    } catch (e) {
      console.error('Falha ao atualizar o dashboard: ' + e.message);
    }
  }

  console.log('Resumo: ' + JSON.stringify(resumo));
  return resumo;
}

/**
 * Processa uma única mensagem: ingestão determinística -> IA -> atualização.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} aba
 * @param {Map} indice
 * @param {GoogleAppsScript.Gmail.GmailMessage} mensagem
 * @param {Object} opcoes
 * @param {Object} resumo
 */
function processarMensagem_(aba, indice, mensagem, opcoes, resumo) {
  let base;

  try {
    base = extrairDadosBase_(mensagem);
  } catch (e) {
    resumo.erros++;
    console.error('Falha ao ler mensagem do Gmail: ' + e.message);
    return;
  }

  const registro = indice.get(base.messageId);
  let numeroLinha;

  if (registro) {
    if (STATUS_REPROCESSAVEIS.indexOf(registro.status) === -1) {
      // Já processada com status final: idempotência.
      resumo.ignorados++;
      if (opcoes.marcarComoLida) {
        marcarComoLida_(mensagem);
      }
      return;
    }

    numeroLinha = registro.linha;
  } else {
    // ETAPA 1 — grava o que a máquina sabe, sem IA.
    numeroLinha = inserirLinhaIngestao_(aba, base);
    indice.set(base.messageId, { linha: numeroLinha, status: STATUS.PENDENTE_IA });
    resumo.linhasIngestao++;
  }

  const processadoEm = new Date();

  try {
    // ETAPA 2 — enriquecimento por IA.
    const prazos = extrairPrazosDoEmail_(base);

    if (!prazos.length) {
      atualizarLinhaIA_(aba, numeroLinha, null, STATUS.SEM_PRAZO, processadoEm);
    } else {
      atualizarLinhaIA_(aba, numeroLinha, prazos[0], prazos[0].status, processadoEm);

      for (let i = 1; i < prazos.length; i++) {
        inserirLinhaPrazoExtra_(aba, base, prazos[i], prazos[i].status, processadoEm);
        resumo.linhasExtras++;
      }
    }

    resumo.processados++;

    if (opcoes.marcarComoLida) {
      marcarComoLida_(mensagem);
    }
  } catch (e) {
    resumo.erros++;

    const status = (e && e.codigo) === STATUS.ERRO_JSON
      ? STATUS.ERRO_JSON
      : STATUS.ERRO_IA;

    // Preserva a linha de ingestão e marca para reprocessamento futuro.
    try {
      atualizarLinhaIA_(aba, numeroLinha, null, status, processadoEm);
    } catch (e2) {
      console.error('Falha ao registrar status ' + status + ': ' + e2.message);
    }

    console.error('Erro ao processar a mensagem ' + base.messageId + ' (' + status + '): ' +
      (e && e.message));
  }
}
