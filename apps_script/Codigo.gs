/**
 * Dashboard da aba DB_Prazos — Google Apps Script
 * =============================================================================
 * Este script NÃO grava dados: a planilha é alimentada pela aplicação Python
 * (app/services/sheets.py). Aqui só existe a camada de apresentação:
 * a Tabela Dinâmica e os indicadores.
 *
 * Instalação (2 minutos):
 *   1. Abra a planilha no Google Sheets.
 *   2. Extensões → Apps Script.
 *   3. Crie o arquivo `Codigo.gs` com este conteúdo e ajuste `appsscript.json`.
 *   4. Rode `configurarPlanilha()` uma vez (autoriza os escopos).
 *   5. Recarregue a planilha: o menu "⚖️ Produtividade Jurídica" aparece.
 *
 * A aba Dashboard é apagada e recriada a cada atualização — trate-a como
 * descartável (não coloque dados manuais nela).
 */

const CONFIG = {
  ABA_DADOS: 'DB_Prazos',
  ABA_DASHBOARD: 'Dashboard',

  /**
   * Cabeçalho esperado (mesma ordem de app/services/sheets.py).
   * O script localiza as colunas POR NOME, então reordenar não quebra nada.
   */
  COLUNAS: {
    ID: 'ID do Prazo',
    PROCESSO: 'Número do Processo',
    ADVOGADO: 'Advogado',
    TIPO: 'Tipo de Prazo',
    DATA: 'Data do Prazo',
    STATUS: 'Status',
    RECEBIDO: 'Data Recebimento (E-mail)',
    REMETENTE_EMAIL: 'Remetente (E-mail)',
    REMETENTE_NOME: 'Remetente (Nome)',
    ASSUNTO: 'Assunto',
    MESSAGE_ID: 'Gmail Message ID',
    ATUALIZADO: 'Atualizado Em'
  },

  COR: {
    AZUL_ESCURO: '#1f3864',
    AZUL_MEDIO: '#2e6fb7',
    CINZA: '#666666'
  }
};

/**
 * Menu na planilha.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('⚖️ Produtividade Jurídica')
    .addItem('Atualizar Dashboard', 'atualizarDashboard')
    .addItem('Reconfigurar planilha', 'configurarPlanilha')
    .addSeparator()
    .addItem('Diagnóstico', 'diagnosticar')
    .addToUi();
}

/**
 * Prepara cabeçalho/formatos e (re)cria o dashboard.
 */
function configurarPlanilha() {
  const aba = obterAbaDados_();
  garantirCabecalho_(aba);
  aplicarFormatos_(aba);
  atualizarDashboard();

  SpreadsheetApp.getUi().alert(
    'Planilha configurada.\n\n' +
    'Aba de dados: ' + aba.getName() + '\n' +
    'Linhas: ' + Math.max(aba.getLastRow() - 1, 0)
  );
}

/**
 * (Re)constrói a aba Dashboard com a Tabela Dinâmica:
 *   Linhas  -> Advogado
 *   Colunas -> Tipo de Prazo
 *   Valores -> COUNTA(Número do Processo)
 *   Filtro  -> Data do Prazo
 */
function atualizarDashboard() {
  const planilha = SpreadsheetApp.getActive();
  const abaDados = obterAbaDados_();
  const ultimaLinha = abaDados.getLastRow();
  const ultimaColuna = abaDados.getLastColumn();

  const anterior = planilha.getSheetByName(CONFIG.ABA_DASHBOARD);
  if (anterior) {
    planilha.deleteSheet(anterior);
  }

  const dashboard = planilha.insertSheet(CONFIG.ABA_DASHBOARD);
  dashboard.setTabColor(CONFIG.COR.AZUL_ESCURO);
  dashboard.setColumnWidth(1, 260);

  dashboard.getRange('A1')
    .setValue('Dashboard de Produtividade — Prazos por Advogado e Tipo')
    .setFontSize(14)
    .setFontWeight('bold')
    .setFontColor(CONFIG.COR.AZUL_ESCURO);

  dashboard.getRange('A2')
    .setValue('Atualizado em ' + Utilities.formatDate(
      new Date(),
      Session.getScriptTimeZone(),
      'dd/MM/yyyy HH:mm'
    ) + ' · fonte: ' + abaDados.getName())
    .setFontSize(9)
    .setFontColor(CONFIG.COR.CINZA);

  if (ultimaLinha < 2) {
    dashboard.getRange('A4')
      .setValue('Ainda não há dados em ' + abaDados.getName() +
        '. Rode a sincronização no Python (python -m app.cli sincronizar).')
      .setFontStyle('italic')
      .setFontColor('#999999');
    return dashboard;
  }

  const origem = abaDados.getRange(1, 1, ultimaLinha, ultimaColuna);
  const pivo = origem.createPivotTable(dashboard.getRange('A4'), 'PivoPrazos');

  pivo.addRowGroup(coluna_(abaDados, CONFIG.COLUNAS.ADVOGADO, ultimaLinha));
  pivo.addColumnGroup(coluna_(abaDados, CONFIG.COLUNAS.TIPO, ultimaLinha));
  pivo.addPivotValue(
    coluna_(abaDados, CONFIG.COLUNAS.PROCESSO, ultimaLinha),
    SpreadsheetApp.PivotTableSummarizeFunction.COUNTA
  );

  aplicarFiltroData_(pivo, abaDados, ultimaLinha);

  return dashboard;
}

/**
 * Filtro da Data do Prazo.
 *
 * O critério vazio (`newFilterCriteria().build()`) equivale a "mostrar tudo",
 * mas em algumas contas o addFilter() o rejeita; nesse caso caímos para
 * `whenCellNotEmpty()`, que oculta apenas as linhas sem data.
 */
function aplicarFiltroData_(pivo, abaDados, ultimaLinha) {
  const faixaData = coluna_(abaDados, CONFIG.COLUNAS.DATA, ultimaLinha);

  try {
    pivo.addFilter(faixaData, SpreadsheetApp.newFilterCriteria().build());
    return;
  } catch (e) {
    console.warn('Critério vazio recusado: ' + e.message);
  }

  try {
    pivo.addFilter(
      faixaData,
      SpreadsheetApp.newFilterCriteria().whenCellNotEmpty().build()
    );
  } catch (e2) {
    console.error('Não foi possível aplicar o filtro de Data do Prazo: ' + e2.message);
  }
}

/**
 * Diagnóstico rápido: colunas encontradas, quantidade de linhas e status.
 */
function diagnosticar() {
  const aba = obterAbaDados_();
  const ultimaLinha = aba.getLastRow();
  const cabecalho = ultimaLinha > 0
    ? aba.getRange(1, 1, 1, aba.getLastColumn()).getValues()[0]
    : [];

  const faltando = Object.keys(CONFIG.COLUNAS)
    .map(function (chave) { return CONFIG.COLUNAS[chave]; })
    .filter(function (nome) { return cabecalho.indexOf(nome) === -1; });

  let contagem = {};

  if (ultimaLinha > 1) {
    const indiceStatus = cabecalho.indexOf(CONFIG.COLUNAS.STATUS);

    if (indiceStatus !== -1) {
      const valores = aba.getRange(2, indiceStatus + 1, ultimaLinha - 1, 1).getValues();
      valores.forEach(function (linha) {
        const chave = String(linha[0] || '(vazio)');
        contagem[chave] = (contagem[chave] || 0) + 1;
      });
    }
  }

  const linhasStatus = Object.keys(contagem).map(function (k) {
    return '  • ' + k + ': ' + contagem[k];
  }).join('\n');

  SpreadsheetApp.getUi().alert(
    'Aba: ' + aba.getName() + '\n' +
    'Linhas de dados: ' + Math.max(ultimaLinha - 1, 0) + '\n' +
    'Colunas faltando: ' + (faltando.length ? faltando.join(', ') : 'nenhuma') + '\n\n' +
    'Status:\n' + (linhasStatus || '  (sem dados)')
  );
}

/* ========================================================================== *
 * Auxiliares
 * ========================================================================== */

/**
 * Aba de dados (cria se não existir).
 */
function obterAbaDados_() {
  const planilha = SpreadsheetApp.getActive();
  let aba = planilha.getSheetByName(CONFIG.ABA_DADOS);

  if (!aba) {
    aba = planilha.insertSheet(CONFIG.ABA_DADOS);
  }

  return aba;
}

/**
 * Garante o cabeçalho esperado na linha 1.
 */
function garantirCabecalho_(aba) {
  const esperado = Object.keys(CONFIG.COLUNAS).map(function (chave) {
    return CONFIG.COLUNAS[chave];
  });

  const atual = aba.getLastColumn() > 0
    ? aba.getRange(1, 1, 1, esperado.length).getValues()[0]
    : [];

  const igual = esperado.every(function (titulo, indice) {
    return String(atual[indice] || '').trim() === titulo;
  });

  if (igual) {
    aba.setFrozenRows(1);
    return false;
  }

  aba.getRange(1, 1, 1, esperado.length)
    .setValues([esperado])
    .setFontWeight('bold')
    .setFontColor('#ffffff')
    .setBackground(CONFIG.COR.AZUL_ESCURO);

  aba.setFrozenRows(1);
  return true;
}

/**
 * Formatos de data nas colunas correspondentes.
 */
function aplicarFormatos_(aba) {
  const ultimaLinha = aba.getLastRow();
  if (ultimaLinha < 2) {
    return;
  }

  const cabecalho = aba.getRange(1, 1, 1, aba.getLastColumn()).getValues()[0];

  [
    { nome: CONFIG.COLUNAS.DATA, formato: 'dd/MM/yyyy' },
    { nome: CONFIG.COLUNAS.RECEBIDO, formato: 'dd/MM/yyyy HH:mm' },
    { nome: CONFIG.COLUNAS.ATUALIZADO, formato: 'dd/MM/yyyy HH:mm' }
  ].forEach(function (item) {
    const indice = cabecalho.indexOf(item.nome) + 1;
    if (indice > 0) {
      aba.getRange(2, indice, ultimaLinha - 1, 1).setNumberFormat(item.formato);
    }
  });
}

/**
 * Range de uma coluna identificada pelo cabeçalho.
 */
function coluna_(aba, nome, ultimaLinha) {
  const cabecalho = aba.getRange(1, 1, 1, aba.getLastColumn()).getValues()[0];
  const indice = cabecalho.indexOf(nome);

  if (indice === -1) {
    throw new Error('Coluna não encontrada na planilha: ' + nome);
  }

  return aba.getRange(1, indice + 1, ultimaLinha, 1);
}
