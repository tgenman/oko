export class ReaderError extends Error {
  detail: unknown;
  code: string | null = null;
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
    const envelope=value&&typeof value==='object'?value:null;
    const message=envelope&&'message' in envelope&&typeof envelope.message==='string'?envelope.message:undefined;
    const error=new ReaderError(message ?? (typeof detail==='string'?detail:response.status===422?'Проверьте значения настроек':'Связь с Око потеряна'));
    if(envelope&&'code' in envelope&&typeof envelope.code==='string')error.code=envelope.code;
    if(envelope&&'diagnostic_id' in envelope&&typeof envelope.diagnostic_id==='string')error.diagnosticId=envelope.diagnostic_id;
    if(envelope&&'command_id' in envelope&&typeof envelope.command_id==='string')error.actionId=envelope.command_id;
    error.detail=envelope&&'details' in envelope?envelope.details:detail;error.confirmed=true;
    error.actionId=response.headers.get('X-Oko-Action') ?? error.actionId;error.diagnosticId=response.headers.get('X-Oko-Error') ?? error.diagnosticId;
    if(response.headers.get('X-Oko-Expected'))error.name='AbortError';
    throw error;
  }
  return value;
}

export const errorMessage=(error:unknown)=>error instanceof Error?error.message:String(error);
