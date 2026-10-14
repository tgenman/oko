export class ReaderError extends Error {
  detail: unknown;
  confirmed = false;
  actionId: string | null = null;
  diagnosticId: string | null = null;
  materialIds: string[] = [];
}

export async function decodeJson(response:Response):Promise<unknown> {
  let value:unknown;
  try {value=await response.json();}
  catch {
    const error=new ReaderError('Некорректный ответ Око');
    error.diagnosticId=response.headers.get('X-Oko-Error');
    if(response.headers.get('X-Oko-Expected'))error.name='AbortError';
    throw error;
  }
  if(!response.ok) {
    const detail=value&&typeof value==='object'&&'detail' in value?value.detail:undefined;
    const error=new ReaderError(typeof detail==='string'?detail:response.status===422?'Проверьте значения настроек':'Связь с Око потеряна');
    error.detail=detail;error.confirmed=true;
    error.actionId=response.headers.get('X-Oko-Action');error.diagnosticId=response.headers.get('X-Oko-Error');
    if(response.headers.get('X-Oko-Expected'))error.name='AbortError';
    throw error;
  }
  return value;
}

export const errorMessage=(error:unknown)=>error instanceof Error?error.message:String(error);
