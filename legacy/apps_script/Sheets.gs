/**
 * Sheets.gs
 * -----------------------------------------------------------------------------
 * Acesso à planilha: abas, cabeçalhos, índice de idempotência e escrita de linhas.
 *
 * Estratégia de escrita em duas etapas:
 *   1. inserirLinhaIngestao_()  -> grava apenas dados determinísticos do Gmail
 *   2. atualizarLinhaIA_()      -> grava o resultado da IA na mesma linha
 */

/**
 * Abre a planilha do projeto.
 * Prioriza a planilha vinculada (container-bound); se o script for standalone,
 * usa o ID salvo em Script Properties.
 * @return {GoogleAppsScript.Spreadsheet.Spreadsheet}
 */
function obterPlanilha_() {
  const ativa = SpreadsheetApp.getActiveSpreadsheet();
  if (ativa) {
    return ativa;
  }

  const id = PropertiesService.getScriptProperties()
    .getProperty(CONFIG.PROPS.SPREADSHEET_ID);

  if (id) {
    return SpreadsheetApp.openById(id);
  }

  throw new Error(
    'Nenhuma planilha disponível. Vincule o script a uma planilha ' +
    'ou defina a propriedade de script ' + CONFIG.PROPS.SPREADSHEET_ID + '.'
  );
}

/**
 * Obtém (ou cria) a aba DB_Prazos já com o cabeçalho correto.
 * @return {GoogleAppsScript.Spreadsheet.Sheet}
 */
function obterAbaDB_() {
  const planilha = obterPlanilha_();
  let aba = planilha.getSheetByName(CONFIG.ABAS.DB);

  if (!aba) {
    aba = planilha.insertSheet(CONFIG.ABAS.DB);
  }

  garantirCabecalho_(aba);
  return aba;
}

/**
 * Garante que a primeira linha contenha exatamente os cabeçalhos esperados.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} aba
 */
function garantirCabecalho_(aba) {
  const esperado = linhaCabecalho_();
  const atual = aba.getLastColumn() > 0
    ? aba.getRange(1, 1, 1, esperado.length).getValues()[0]
    : [];

  const igual = esperado.every(function (titulo, i) {
    return String(atual[i] || '').trim() === titulo;
  });

  if (igual) {
    return;
  }

  aba.getRange(1, 1, 1, esperado.length)
    .setValues([esperado])
    .setFontWeight('bold')
    .setFontColor('#ffffff')
    .setBackground('#1f3864')
    .setVerticalAlignment('middle');

  aba.setFrozenRows(1);
  aplicarFormatosColunas_(aba);
}

/**
 * Aplica formatos de data/numeração nas colunas de data.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} aba
 */
function aplicarFormatosColunas_(aba) {
  const linhas = Math.max(aba.getMaxRows() - 1, 1);

  aba.getRange(2, COL.RECEBIDO, linhas, 1).setNumberFormat('dd/MM/yyyy HH:mm');
  aba.getRange(2, COL.DATA, linhas, 1).setNumberFormat('dd/MM/yyyy');
  aba.getRange(2, COL.PROCESSADO, linhas, 1).setNumberFormat('dd/MM/yyyy HH:mm');
}

/**
 * Carrega o índice de idempotência: Message ID -> { linha, status }.
 * Lê a planilha uma única vez por execução (evita N leituras).
 * @param {GoogleAppsScript.Spreadsheet.Sheet} aba
 * @return {Map<string, {linha: number, status: string}>}
 */
function obterIndiceMensagens_(aba) {
  const indice = new Map();
  const ultimaLinha = aba.getLastRow();

  if (ultimaLinha < 2) {
    return indice;
  }

  const largura = COL.STATUS - COL.ID + 1;
  const dados = aba.getRange(2, COL.ID, ultimaLinha - 1, largura).getValues();
  const posStatus = COL.STATUS - COL.ID;

  for (let i = 0; i < dados.length; i++) {
    const id = String(dados[i][0] || '').trim();

    if (!id || indice.has(id)) {
      continue;
    }

    indice.set(id, {
      linha: i + 2,
      status: String(dados[i][posStatus] || '').trim()
    });
  }

  return indice;
}

/**
 * Monta o array completo (A..L) de uma linha da DB_Prazos.
 * @param {Object} base Dados determinísticos extraídos do e-mail.
 * @param {Object|null} prazo Prazo normalizado (ou null).
 * @param {string} status Status da extração.
 * @param {Date} processadoEm Timestamp do processamento.
 * @return {Array} Array com TOTAL_COLUNAS posições.
 */
function montarLinha_(base, prazo, status, processadoEm) {
  const linha = new Array(TOTAL_COLUNAS).fill('');

  linha[COL.ID - 1] = base.messageId;
  linha[COL.THREAD - 1] = base.threadId;
  linha[COL.RECEBIDO - 1] = base.dataRecebimento;
  linha[COL.REM_NOME - 1] = base.remetenteNome;
  linha[COL.REM_EMAIL - 1] = base.remetenteEmail;
  linha[COL.ASSUNTO - 1] = base.assunto;

  if (prazo) {
    linha[COL.ADVOGADO - 1] = prazo.advogado;
    linha[COL.TIPO - 1] = prazo.tipo_prazo;
    linha[COL.DATA - 1] = prazo.data;
    linha[COL.PROCESSO - 1] = prazo.numero_processo;
  }

  linha[COL.STATUS - 1] = status;
  linha[COL.PROCESSADO - 1] = processadoEm;

  return linha;
}

/**
 * ETAPA 1 — grava a linha de ingestão, antes de qualquer chamada à IA.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} aba
 * @param {Object} base
 * @return {number} Índice (1-based) da linha criada.
 */
function inserirLinhaIngestao_(aba, base) {
  const linha = montarLinha_(base, null, STATUS.PENDENTE_IA, new Date());
  const numeroLinha = aba.getLastRow() + 1;

  aba.getRange(numeroLinha, 1, 1, TOTAL_COLUNAS).setValues([linha]);
  return numeroLinha;
}

/**
 * Anexa uma linha adicional para e-mails com múltiplos prazos.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} aba
 * @param {Object} base
 * @param {Object} prazo
 * @param {string} status
 * @param {Date} processadoEm
 * @return {number}
 */
function inserirLinhaPrazoExtra_(aba, base, prazo, status, processadoEm) {
  const linha = montarLinha_(base, prazo, status, processadoEm);
  const numeroLinha = aba.getLastRow() + 1;

  aba.getRange(numeroLinha, 1, 1, TOTAL_COLUNAS).setValues([linha]);
  return numeroLinha;
}

/**
 * ETAPA 2 — atualiza o bloco de IA (colunas G..L) de uma linha existente,
 * preservando integralmente os dados de ingestão (A..F).
 * @param {GoogleAppsScript.Spreadsheet.Sheet} aba
 * @param {number} numeroLinha
 * @param {Object|null} prazo
 * @param {string} status
 * @param {Date} processadoEm
 */
function atualizarLinhaIA_(aba, numeroLinha, prazo, status, processadoEm) {
  const valores = [
    prazo ? prazo.advogado : '',
    prazo ? prazo.tipo_prazo : '',
    prazo ? prazo.data : '',
    prazo ? prazo.numero_processo : '',
    status,
    processadoEm
  ];

  aba.getRange(numeroLinha, COL.ADVOGADO, 1, TOTAL_COLUNAS - COL.ADVOGADO + 1)
    .setValues([valores]);
}
