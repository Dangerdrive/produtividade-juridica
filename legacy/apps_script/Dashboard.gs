/**
 * Dashboard.gs
 * -----------------------------------------------------------------------------
 * Reconstrói a aba `Dashboard` com uma Tabela Dinâmica criada por código:
 *   Linhas  -> Advogado
 *   Colunas -> Tipo de Prazo
 *   Valores -> COUNTA(Número do Processo)
 *   Filtro  -> Data do Prazo
 *
 * A aba é apagada e recriada a cada atualização. Isso garante que a tabela
 * sempre leia o intervalo de origem completo (A1:L{última linha}) e evita
 * depender de APIs instáveis de listagem de tabelas dinâmicas.
 */

/**
 * Reconstrói a aba Dashboard.
 * @return {GoogleAppsScript.Spreadsheet.Sheet}
 */
function atualizarDashboard_() {
  const planilha = obterPlanilha_();
  const abaDB = obterAbaDB_();
  const ultimaLinha = abaDB.getLastRow();

  const anterior = planilha.getSheetByName(CONFIG.ABAS.DASHBOARD);

  if (anterior) {
    planilha.deleteSheet(anterior);
  }

  const dashboard = planilha.insertSheet(CONFIG.ABAS.DASHBOARD);
  dashboard.setTabColor('#1f3864');
  dashboard.setColumnWidth(1, 260);

  dashboard.getRange('A1')
    .setValue('Dashboard de Produtividade — Prazos por Advogado e Tipo')
    .setFontSize(14)
    .setFontWeight('bold')
    .setFontColor('#1f3864');

  dashboard.getRange('A2')
    .setValue('Atualizado em ' +
      Utilities.formatDate(new Date(), CONFIG.FUSO, 'dd/MM/yyyy HH:mm') +
      ' · Fonte: ' + CONFIG.ABAS.DB)
    .setFontSize(9)
    .setFontColor('#666666');

  if (ultimaLinha < 2) {
    dashboard.getRange('A4')
      .setValue('Ainda não há dados em ' + CONFIG.ABAS.DB +
        '. Importe o histórico ou aguarde novos e-mails.')
      .setFontStyle('italic')
      .setFontColor('#999999');
    return dashboard;
  }

  criarTabelaDinamica_(abaDB, dashboard, ultimaLinha);
  return dashboard;
}

/**
 * Cria a tabela dinâmica no ponto de ancoragem A4 da aba Dashboard.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} abaDB
 * @param {GoogleAppsScript.Spreadsheet.Sheet} dashboard
 * @param {number} ultimaLinha
 */
function criarTabelaDinamica_(abaDB, dashboard, ultimaLinha) {
  const origem = abaDB.getRange(1, 1, ultimaLinha, TOTAL_COLUNAS);
  const ancora = dashboard.getRange('A4');

  const pivo = origem.createPivotTable(ancora, 'PivoPrazos');

  pivo.addRowGroup(abaDB.getRange(1, COL.ADVOGADO, ultimaLinha, 1));
  pivo.addColumnGroup(abaDB.getRange(1, COL.TIPO, ultimaLinha, 1));
  pivo.addPivotValue(
    abaDB.getRange(1, COL.PROCESSO, ultimaLinha, 1),
    SpreadsheetApp.PivotTableSummarizeFunction.COUNTA
  );

  aplicarFiltroDataPrazo_(pivo, abaDB, ultimaLinha);
}

/**
 * Aplica o filtro da Data do Prazo.
 *
 * O planejamento pede `SpreadsheetApp.newFilterCriteria().build()`. Em algumas
 * contas/versões essa critéria vazia é rejeitada pelo addFilter(); nesse caso
 * caímos para `whenCellNotEmpty()`, que produz o mesmo efeito prático
 * (ocultar linhas sem data do prazo).
 * @param {GoogleAppsScript.Spreadsheet.PivotTable} pivo
 * @param {GoogleAppsScript.Spreadsheet.Sheet} abaDB
 * @param {number} ultimaLinha
 */
function aplicarFiltroDataPrazo_(pivo, abaDB, ultimaLinha) {
  const colunaData = abaDB.getRange(1, COL.DATA, ultimaLinha, 1);

  try {
    pivo.addFilter(colunaData, SpreadsheetApp.newFilterCriteria().build());
    return;
  } catch (e) {
    console.warn('FilterCriteria vazia recusada (' + e.message +
      '). Aplicando filtro "célula não vazia" na Data do Prazo.');
  }

  try {
    pivo.addFilter(
      colunaData,
      SpreadsheetApp.newFilterCriteria().whenCellNotEmpty().build()
    );
  } catch (e2) {
    console.error('Não foi possível aplicar o filtro de Data do Prazo: ' + e2.message);
  }
}
