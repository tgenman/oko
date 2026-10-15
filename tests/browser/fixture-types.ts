import type {Card, Material, Status, Subscriptions, Navigation, EffectiveSettings, YouTube, DiagnosticJournal, CodexState, ProcessorConfig, History} from '../../frontend/src/api/legacy-generated/types.gen';

// Scenarios deliberately omit optional fields and inject malformed HTTP replies.
// Keep those payloads unknown at the transport boundary; successful snapshots
// are additionally checked against the exported OpenAPI schemas at delivery.
export type FixtureMaterial = Partial<Material & Card> & Pick<Material, 'id' | 'title' | 'text' | 'feed_id' | 'published' | 'complete' | 'url'>;
export interface FixtureReply<T = unknown> {body?: T; status?: number; next?: [string, FixtureReply][]; base64?: string; mime?: string;}
export type ApiReply<P extends string> = P extends 'cards' ? Card[] : P extends 'navigation' ? Navigation : P extends 'import' ? Subscriptions : P extends 'youtube' ? YouTube : P extends 'subscriptions' ? Subscriptions : P extends `settings/${string}` ? EffectiveSettings : P extends `materials/${string}` ? Material : P extends 'status' ? Status : P extends 'debug' ? DiagnosticJournal : P extends 'processors/codex' ? CodexState : P extends 'processors/llamacpp' ? ProcessorConfig : P extends `feeds/${string}/history` ? History : unknown;
type Snapshot<P extends string> = P extends '/api/v3/cards' ? Partial<Card>[] : P extends '/api/v3/reading-composition' ? Navigation : P extends '/api/status' ? Status : P extends '/api/subscriptions' ? Subscriptions : P extends `${string}/history` ? History : P extends `/api/v3/settings/${string}` ? EffectiveSettings : unknown;
export interface ReplyMap extends Map<string, FixtureReply> {get<P extends string>(path: P): FixtureReply<Snapshot<P>> | undefined;}
export interface ReaderTransport {
  replies: ReplyMap;
  held: Map<string, boolean>;
  pending: Map<string, (value: Response | Promise<Response>)=>void>;
  aliases: Map<string, string>;
  native: Set<string>;
  requests: {url: string; method: string; body: Record<string, unknown> | null}[];
  release(url: string, body: unknown, status?: number): void;
}
declare global {
  var readerTransport: ReaderTransport;
  var browserResources: {created: string[]; revoked: string[]};
  var testVideo: HTMLVideoElement;
  interface Window {
    readerTransport: ReaderTransport;
    readingFixtureReply(url:string,method:string,body:unknown,status:number):unknown;
    browserResources: typeof browserResources;
    testVideo: HTMLVideoElement;
    readerFixtures: {history(values: Partial<History & {feed_id: string}>): History & {feed_id?: string}};
    validateReaderReply(url: string, method: string, status: number, body: unknown, alias?: string, record?: boolean): Promise<void>;
    releaseOldMenu(): void;
    interruptedCatalogReply(value: Response): void;
    lateYouTubeReply(value: Response): void;
    closingRequests: {url: string; method?: string; keepalive?: boolean; authorization: string | null}[];
    refreshProbe: {enabled: boolean; oldStarted: boolean; feedRequests: number};
  }
}
