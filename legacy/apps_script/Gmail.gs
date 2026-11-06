/**
 * Gmail.gs
 * -----------------------------------------------------------------------------
 * Busca de mensagens e extração dos metadados DETERMINÍSTICOS do e-mail
 * (remetente, data de recebimento, assunto, IDs) — sem uso de IA.
 */

/**
 * Busca mensagens no Gmail em páginas de threads.
 * @param {string} query Sintaxe de busca do Gmail.
 * @param {number} offset Deslocamento (threads já percorridas).
 * @param {number} tamanhoPagina Quantidade máxima de threads.
 * @return {GoogleAppsScript.Gmail.GmailThread[]}
 */
function buscarThreads_(query, offset, tamanhoPagina) {
  return GmailApp.search(query, offset, tamanhoPagina);
}

/**
 * Achata as threads em mensagens, em ordem cronológica crescente.
 * @param {GoogleAppsScript.Gmail.GmailThread[]} threads
 * @return {GoogleAppsScript.Gmail.GmailMessage[]}
 */
function mensagensDasThreads_(threads) {
  const mensagens = [];

  for (let i = 0; i < threads.length; i++) {
    const doThread = threads[i].getMessages();
    for (let j = 0; j < doThread.length; j++) {
      mensagens.push(doThread[j]);
    }
  }

  mensagens.sort(function (a, b) {
    return a.getDate().getTime() - b.getDate().getTime();
  });

  return mensagens;
}

/**
 * Separa "Nome Sobrenome <email@dominio>" em nome e e-mail.
 * @param {string} from Cabeçalho "From" bruto.
 * @return {{nome: string, email: string}}
 */
function separarRemetente_(from) {
  const bruto = String(from || '').trim();
  const comNome = bruto.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/);

  if (comNome) {
    return {
      nome: comNome[1].trim(),
      email: comNome[2].trim().toLowerCase()
    };
  }

  const temArroba = bruto.indexOf('@') !== -1;
  return {
    nome: temArroba ? bruto.split('@')[0] : bruto,
    email: temArroba ? bruto.toLowerCase() : ''
  };
}

/**
 * Extrai os dados que o próprio Gmail fornece, sem nenhuma inferência.
 * @param {GoogleAppsScript.Gmail.GmailMessage} mensagem
 * @return {Object}
 */
function extrairDadosBase_(mensagem) {
  const remetente = separarRemetente_(mensagem.getFrom());

  return {
    messageId: mensagem.getId(),
    threadId: mensagem.getThread().getId(),
    dataRecebimento: mensagem.getDate(),
    remetenteNome: remetente.nome || remetente.email,
    remetenteEmail: remetente.email,
    assunto: mensagem.getSubject() || '',
    corpo: String(mensagem.getPlainBody() || '').trim()
  };
}

/**
 * Marca a mensagem como lida (somente após status final de processamento).
 * @param {GoogleAppsScript.Gmail.GmailMessage} mensagem
 * @return {boolean} true se houve alteração.
 */
function marcarComoLida_(mensagem) {
  if (!mensagem.isUnread()) {
    return false;
  }

  mensagem.markRead();
  return true;
}
