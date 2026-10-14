// Request rules only: UI lifetime and diagnostic delivery belong to callers.
export function createTransport({fetch, token, origin}) {
  function headers(path, json = false) {
    const result = json ? {'Content-Type':'application/json'} : {};
    if (new URL(path, origin).origin === origin) result.Authorization = 'Bearer ' + token;
    return result;
  }
  async function decode(response) {
    let result;
    try { result = await response.json(); }
    catch {
      const error = new Error('Некорректный ответ Око');
      error.diagnosticId = response.headers.get('X-Oko-Error');
      if (response.headers.get('X-Oko-Expected')) error.name = 'AbortError';
      throw error;
    }
    if (!response.ok) {
      const detail = result?.detail;
      const error = new Error(typeof detail === 'string' ? detail : response.status === 422 ? 'Проверьте значения настроек' : 'Связь с Око потеряна');
      error.detail = detail;
      error.confirmed = true;
      error.actionId = response.headers.get('X-Oko-Action');
      error.diagnosticId = response.headers.get('X-Oko-Error');
      if (response.headers.get('X-Oko-Expected')) error.name = 'AbortError';
      throw error;
    }
    return result;
  }
  async function request(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) await decode(response);
    return response;
  }
  return {
    async json(path, method = 'GET', body = undefined, signal = undefined, actionId = undefined) {
      const response = await request(path, {method, signal, cache:'no-store', headers:{...headers(path, true), ...(actionId ? {'X-Oko-Action':actionId} : {})}, body:body === undefined ? undefined : JSON.stringify(body)});
      return decode(response);
    },
    media(path, signal = undefined, cache = undefined) {
      return fetch(path, {signal, ...(cache === undefined ? {} : {cache}), headers:headers(path)});
    },
    close(path) {
      return request(path, {method:'POST', keepalive:true, headers:headers(path)});
    },
  };
}
