import type {ErrorEnvelope} from './generated/types.gen';

// Only codes with an unambiguous meaning belong here. Other errors retain
// the original message, which may contain actionable diagnostic details.
const messages: Partial<Record<ErrorEnvelope['code'], string>> = {
  runtime_disabled: 'Oko is off',
  runtime_stopped: 'Oko stopped',
  storage_unavailable: 'Storage unavailable',
  session_required: 'Local session required',
  host_not_allowed: 'Address not allowed',
  origin_not_allowed: 'Page not allowed',
  client_session_expired: 'Client session expired',
  media_unavailable: 'Media unavailable',
  version_unavailable: 'Version unavailable',
  command_context_mismatch: 'Command context mismatch',
  folder_name_conflict: 'A folder with this name already exists',
  folder_not_empty: 'Move or delete all feeds before deleting this folder',
  deletion_in_progress: 'Feed is being deleted',
  verification_expired: 'Check the current feed URL first',
};
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
    const error=new ReaderError('Invalid response from Oko');
    error.diagnosticId=response.headers.get('X-Oko-Error');
    if(response.headers.get('X-Oko-Expected'))error.name='AbortError';
    throw error;
  }
  if(!response.ok) {
    const detail=value&&typeof value==='object'&&'detail' in value?value.detail:undefined;
    const envelope=value&&typeof value==='object'?value:null;
    const message=envelope&&'message' in envelope&&typeof envelope.message==='string'?envelope.message:undefined;
    const error=new ReaderError(message ?? (typeof detail==='string'?detail:response.status===422?'Check your settings':'Connection to Oko lost'));
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

export const errorMessage=(error:unknown)=>{
  if(error instanceof ReaderError&&error.code&&Object.hasOwn(messages,error.code))
    return messages[error.code as ErrorEnvelope['code']]!;
  return error instanceof Error?error.message:String(error);
};
