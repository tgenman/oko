import type * as Wire from '../../frontend/src/api/generated/types.gen';
import type {Card, Material, Status, Subscriptions, Navigation, EffectiveSettings, History} from './scenario-data';

// Scenarios deliberately omit optional fields and inject malformed HTTP replies.
// Keep those payloads unknown at the transport boundary; successful snapshots
// are additionally checked against the exported OpenAPI schemas at delivery.
export type FixtureMaterial = {post_web_unavailable?:boolean} & Partial<Material & Card> & Pick<Material, 'id' | 'title' | 'text' | 'feed_id' | 'published' | 'complete' | 'url'>;
export interface FixtureReply<T = unknown> {body?: T; status?: number; next?: [string, FixtureReply][]; base64?: string; mime?: string;}
type NativeReply<P extends string> = P extends 'feeds/verify' ? Wire.FeedVerification : P extends 'feeds' ? Wire.SubscriptionFeed : P extends 'client-sessions' ? Wire.ClientSession : P extends 'cards' ? Wire.ReadingCard[] : P extends 'reading-composition' ? Wire.ReadingComposition : P extends 'youtube' ? Wire.YouTubeState : P extends 'subscriptions' ? Wire.SubscriptionCatalog : P extends 'settings/feed-refresh' ? Wire.EffectiveFeedRefreshMinutes : P extends 'settings/telegram-http' ? Wire.EffectiveTelegramHttpRps : P extends `settings/${string}` ? Wire.EffectiveSettingsSnapshot : P extends `materials/${string}` ? Wire.ReadableMaterial : P extends 'status' ? Wire.RuntimeStatus : P extends 'diagnostics' ? Wire.DiagnosticJournal : P extends 'processors/codex' ? Wire.CodexProcessorState : P extends 'processors/llamacpp' ? Wire.LocalProcessorState : P extends `feeds/${string}/history` ? Wire.FeedHistoryState : unknown;
export type ApiReply<P extends string> = NativeReply<P extends `v3/${infer Route}` ? Route : P>;
type Snapshot<P extends string> = P extends '/api/v3/cards' ? Partial<Card>[] : P extends '/api/v3/reading-composition' ? Navigation : P extends '/api/v3/status' ? Status : P extends '/api/v3/subscriptions' ? Subscriptions : P extends `${string}/history` ? History : P extends `/api/v3/settings/${string}` ? EffectiveSettings : unknown;
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
