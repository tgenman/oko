import {createClient} from './generated/client';
import {status} from './generated/sdk.gen';
import type {Status} from './generated/types.gen';

export class ReaderError extends Error {
  detail: unknown;
  confirmed = false;
  actionId: string | null = null;
  diagnosticId: string | null = null;
}

async function checkResponse(response: Response): Promise<void> {
  let value: unknown;
  try {value = await response.clone().json();}
  catch {
    const error = new ReaderError('Некорректный ответ Око');
    error.diagnosticId = response.headers.get('X-Oko-Error');
    if (response.headers.get('X-Oko-Expected')) error.name = 'AbortError';
    throw error;
  }
  if (response.ok) return;
  const detail = value && typeof value === 'object' && 'detail' in value ? value.detail : undefined;
  const error = new ReaderError(typeof detail === 'string' ? detail : response.status === 422 ? 'Проверьте значения настроек' : 'Связь с Око потеряна');
  error.detail = detail;
  error.confirmed = true;
  error.actionId = response.headers.get('X-Oko-Action');
  error.diagnosticId = response.headers.get('X-Oko-Error');
  if (response.headers.get('X-Oko-Expected')) error.name = 'AbortError';
  throw error;
}

export function createReaderApi({fetch: adapter, token, origin}: {fetch: typeof fetch; token: string | null; origin: string}) {
  const client = createClient({baseUrl: origin, cache:'no-store', throwOnError:true,
    fetch: async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const url = new URL(request.url);
      if (url.origin !== origin) throw new ReaderError('Посторонний адрес API');
      const headers = new Headers(request.headers);
      headers.set('Authorization','Bearer '+token);
      // The adapter keeps the same public fetch boundary as the previous
      // reader, including controlled delayed replies used by browser checks.
      const response = await adapter(url.pathname+url.search, {
        method:request.method, headers, signal:request.signal, cache:'no-store',
        keepalive:request.keepalive,
        ...(request.body ? {body:await request.text()} : {}),
      });
      await checkResponse(response);
      return response;
    },
  });
  return {
    client,
    async status(signal?: AbortSignal): Promise<Status> {
      const reply = await status({client, signal});
      return reply.data;
    },
  };
}
