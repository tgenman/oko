from oko.storage.database import INTEGRITY_ERRORS
"""Application actions and reader state; background scheduling stays outside."""
from oko.storage.reading import reading_snapshot
import asyncio
from dataclasses import dataclass
from oko.domain.processing import ProcessingWait
from collections.abc import Awaitable, Callable
import secrets
from typing import Any, Literal, TypeVar
import httpx
from oko.collection.rss import RSSSource, feed_title, normalized
from oko.collection.contracts import SourceError
from oko.collection.contracts import Source
from oko.processing.llamacpp import LlamaCpp, ProcessorConfig
from oko.processing.contracts import ProcessingError
from oko.processing.execution import CodexSettings
from oko.processing.codex import Codex, CodexClient, status as codex_status
from oko.collection.telegram.http import TelegramHTTP
from oko.storage.store import Store
from oko.collection.telegram.contracts import TelegramBinding
from oko.collection.telegram.source import LocalTelegram, TelegramClient
from oko.subscriptions.subscriptions import AddFeed, FeedURL, Overrides, FeedPatch, FolderConflict, identify
from oko.subscriptions.history import InitialHistory
from oko.subscriptions.telegram import TelegramConflict
from oko.diagnostics import Journal, diagnostic_source, record_caught
from oko.execution import Execution, ActionError, _ExternalCheck
from oko.workers.control import JobControl
from oko.storage.transaction import transaction
from oko.services.subscriptions import SubscriptionService
from oko.services.queries import ReadingQueries
from oko.services.reading import ReadingService
from oko.services.connections import ConnectionService
from oko.services.commands import CommandJournal

T = TypeVar('T')

@dataclass(frozen=True)
class StatusSnapshot:
    state: dict[str, Any]
    bindings: dict[str, dict[str, Any]]


class ReaderApplication:
    def __init__(self, execution: Execution, jobs: JobControl, *, clock: Callable[[], float], wall_clock: Callable[[], float], sources: dict[str, Source], bindings: dict[str, TelegramBinding], sync_sources: Callable[[], None], interval_changed: Callable[[float], None], source_description: Callable[[str], str | None], rss_transport: httpx.AsyncBaseTransport | None, ai_transport: httpx.AsyncBaseTransport | None, telegram: TelegramClient | None, telegram_http: TelegramHTTP, codex: CodexClient, stop: Callable[[], Awaitable[None]], admission_changed: Callable[[], Awaitable[None]], set_power: Callable[[bool], Awaitable[None]]) -> None:
        self.execution, self.jobs = execution, jobs
        self.subscriptions = SubscriptionService(execution.require_store)
        self.queries = ReadingQueries(execution.require_store)
        self.reading = ReadingService(execution.require_store)
        self.connections = ConnectionService(execution.require_store)
        self.commands = CommandJournal(execution.require_store, wall_clock)
        self.final_queries = ReadingQueries(execution.retained_store)
        self._actions, self._clock, self._wall_clock = execution._actions, clock, wall_clock
        self._sources, self._telegram_bindings = sources, bindings
        self._sync_sources, self._interval_changed = sync_sources, interval_changed
        self._source_description = source_description
        self._rss_transport, self._ai_transport = rss_transport, ai_transport
        self._telegram_client, self._telegram_http, self._codex_client = telegram, telegram_http, codex
        self.stop, self.admission_changed, self.set_power = stop, admission_changed, set_power
        self._cancel, self._drain_deletions = jobs.cancel, jobs._drain_deletions
        self._feed_checks: dict[str, str] = {}
        self._last_problems: dict[str, Any] = dict(current=[], recent=[], count=0, updated_at=wall_clock())
        self._viewed_ids: list[str] = []
        self._codex_status = codex_status()
        self._collection_instance = secrets.token_hex(16)
        self._collection_revision = 0

    @property
    def _store(self) -> Store | None:
        return self.execution._store

    @property
    def _error(self) -> str | None:
        return self.execution._error

    @property
    def has_storage(self) -> bool:
        return self.execution._store is not None

    @property
    def enabled(self) -> bool:
        return self.execution.enabled

    @property
    def stopped(self) -> bool:
        return self.execution.stopped

    @property
    def diagnostics(self) -> Journal | None:
        return self.execution.diagnostics

    @property
    def reader_finish(self) -> Callable[[], Awaitable[None]] | None:
        return self.execution.reader_finish

    @reader_finish.setter
    def reader_finish(self, value: Callable[[], Awaitable[None]]) -> None:
        self.execution.reader_finish = value

    @property
    def reader_advance(self) -> Callable[[], None] | None:
        return self.execution.reader_advance

    @reader_advance.setter
    def reader_advance(self, value: Callable[[], None]) -> None:
        self.execution.reader_advance = value

    def _live(self) -> bool:
        return self.execution._live()

    def _admitted(self) -> bool:
        return self.execution._admitted()

    def require_work(self) -> None:
        self.execution.require_work()

    def require_store(self) -> Store:
        return self.execution.require_store()

    def material_was_viewed(self, identity: str) -> bool:
        if self._store:
            return self.queries.was_viewed(identity)
        return identity in self._viewed_ids


    def collection_snapshot(self, feed_id: str | None = None) -> dict[str, Any]:
        current = self.require_store()
        feeds = self.queries.feeds(feed_id)
        bindings=self.queries.bindings([feed['id'] for feed in feeds if feed['kind']=='telegram'])
        for feed in feeds:
            binding=bindings.get(feed['id'])
            feed['collection_access'] = 'Веб' if binding and binding['kind']=='web' else 'Личный аккаунт' if binding and binding.get('role')=='personal' else 'Аккаунт для сбора' if binding and binding.get('role')=='collection' else feed.get('transport', feed['kind'])
        sources = current.collection_status.snapshot(feeds, self._live(),
            'Око выключено' if not self.enabled else 'Ожидание присутствия читателя' if not self._admitted() else 'Работа приостановлена',
            {identity: dict(generation=attempt.job.generation, elapsed_seconds=max(0.,self._clock()-attempt.started),stage=attempt.capture.stage)
             for identity, attempt in self.jobs._collection_active.items()}, details=feed_id is not None)
        self._collection_revision += 1
        return dict(instance_id=self._collection_instance, revision=self._collection_revision,
                    observed_at=self._wall_clock(), work_allowed=self._live(), sources=self._clean_collection(sources))


    def status_snapshot(self) -> StatusSnapshot:
        candidates = None
        with reading_snapshot():
            if self._store and not self.stopped:
                candidates = self._store.problems.candidates(self._wall_clock())
            feeds = {f['id']: f for f in self._store.subscriptions.feeds()} if self._store else {}
            bindings=self._store.subscriptions.telegram.bindings(list(feeds)) if self._store and not self.stopped else {}
            state = self._status()
        if candidates is not None and self._store:
            self._last_problems = self._store.problems.reconcile(candidates, self._wall_clock(), feeds)
            if not self._error:
                state['problems'] = self._last_problems
        return StatusSnapshot(state, bindings)


    def _status(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        state['youtube'] = self.youtube_status() if self._store and not self.stopped else None
        if self._store and not self.stopped:
            self._viewed_ids = self.queries.viewed_ids()
        state['problems'] = self._last_problems
        if self._error:
            state['problems'] = dict(current=[dict(id='runtime:storage',code='storage-unavailable',operation='producer:storage',section='action',title='Хранилище недоступно',
                consequence='Сбор и обработка остановлены.',next_step='Проверьте доступность хранилища; состояние не сбрасывается.',
                objects=[],action=None,created_at=self._wall_clock(),next_at=None,state='current',finished_at=None,outcome=None)],recent=[],count=1,updated_at=self._wall_clock())
        state['viewed_ids'] = self._viewed_ids
        source_states = self._store.source_statuses() if self._store else []
        names = {f['id']: f['title'] for f in self._store.subscriptions.feeds()} if self._store else {}
        source_states = [dict(s, title=names.get(s['feed_id'], s['feed_id']), changes=self._source_description(s['feed_id'])) for s in source_states if s['feed_id'] in self._sources]
        return dict(**state, initial_histories=[dict(feed_id=f['id'], **f['initial_history']) for f in self._store.subscriptions.feeds() if f['initial_history']] if self._store else [], telegram_reads=self._store.read_sync.statuses() if self._store and not self.stopped else [], sources=source_states, error=self._error,
                    ready_sources=self._store.ready_sources(list(self._sources)) if self._store and not self.stopped else 0,
                    total_sources=len(self._sources))


    async def action_started(self, identity: str, context: dict[str, Any], *, restart: bool = False, expected_identity: str | None = None) -> None:
        async with self._actions:
            if self.has_storage and not self.stopped:
                with transaction(self.require_store().db):
                    self.commands.started(identity, context, restart, expected_identity)


    async def action_failed(self, identity: str, error_json: str) -> None:
        async with self._actions:
            if self.has_storage and not self.stopped:
                with transaction(self.require_store().db):
                    self.commands.failed(identity, error_json)


    async def action_finished(self, identity: str, succeeded: bool) -> None:
        async with self._actions:
            if self.has_storage and not self.stopped:
                with transaction(self.require_store().db):
                    self.commands.finished(identity, succeeded)


    async def report_action(self, identity: str, context: dict[str, Any], result: str) -> str:
        async with self._actions:
            with transaction(self.require_store().db):
                return self.commands.report(identity, context, result)


    async def abandon_problem(self, identity: str) -> None:
        async with self._actions:
            with transaction(self.require_store().db):
                self.commands.abandon(identity)


    async def collect(self, feed: str | None = None, *, folder: str | None = None) -> None:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            self._sync_sources()
            if feed is not None:
                saved = next((entry for entry in self.queries.feeds() if entry['id']==feed),None)
                if saved is None:
                    raise ActionError('missing','Фид недоступен')
                if saved['connection']=='deleting':
                    raise ActionError('conflict','Фид удаляется',code='deletion_in_progress')
            if folder is not None and not any(f['id']==folder for f in self.queries.folders()):
                raise ActionError('missing','Папка недоступна')
            selected = {f['id'] for f in self.queries.feeds() if f['connection']!='deleting' and (folder is None or f['folder_id']==folder)}
            for source_feed in selected:
                if feed is None or feed==source_feed:
                    self.jobs.refresh_feed(source_feed)


    async def retry_material(self, item: str) -> None:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            with transaction(current.db):
                if not self.jobs.retry_material(item):
                    raise ActionError('missing', 'Материал недоступен')
                affected = {current.item_feed(item)}
                self.jobs.invalidate(affected)
            await self.jobs.cancel(affected, invalidate=False)


    def youtube_status(self) -> dict[str, Any]:
        return self.connections.youtube_status(self._wall_clock())


    async def configure_youtube(self, body: Any) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                with transaction(current.db):
                    self.connections.configure_youtube(body)
                    self.jobs.invalidate()
            except ValueError:
                raise ActionError('invalid', 'Некорректные настройки прокси: нужен HTTP/HTTPS-адрес без учётных данных, пути и параметров', expected=True) from None
            await self.jobs.cancel(invalidate=False)
            return self.youtube_status()


    async def viewed(self, item: str) -> None:
        async with self._actions:
            current = self.require_store()
            with transaction(current.db):
                members = self.reading.viewed(item)
                self.jobs.reconcile_cleanup()
            if self.diagnostics:
                self.diagnostics.forget_materials(members)
            await self._drain_deletions(current)


    async def viewed_many(self, identities: list[str]) -> None:
        """Atomically mark existing cards viewed; repeated or missing IDs are harmless."""
        async with self._actions:
            current = self.require_store()
            with transaction(current.db):
                members = self.reading.viewed_many(identities)
                if members:
                    self.jobs.reconcile_cleanup()
            if self.diagnostics and members:
                self.diagnostics.forget_materials(members)
            if members:
                await self._drain_deletions(current)


    def _completion_preview(self, folder: str, current: Store, scope: Literal['folder', 'feed'] = 'folder') -> dict[str, Any]:
        return self.reading.preview(folder, scope)


    async def preview_completion(self, folder: str) -> dict[str, Any]:
        async with self._actions:
            return self._completion_preview(folder, self.require_store())


    async def preview_feed_completion(self, feed: str) -> dict[str, Any]:
        async with self._actions:
            return self._completion_preview(feed, self.require_store(), 'feed')


    async def complete_feed(self, feed: str, token: str) -> None:
        await self.complete_folder(feed, token, scope='feed')


    async def complete_folder(self, folder: str, token: str, *, scope: Literal['folder', 'feed'] = 'folder') -> None:
        async with self._actions:
            current = self.require_store()
            with transaction(current.db):
                members = self.reading.complete(folder, token, scope)
                self.jobs.reconcile_cleanup()
            self.reading.accepted(folder, token, scope)
            if self.diagnostics and members:
                self.diagnostics.forget_materials(members)
            await self._drain_deletions(current)
            if current.status()['deleting']:
                raise ActionError('unavailable', 'Удаление не завершено. Око повторит попытку; ошибка видна в статусе.')


    async def pause_history(self, identity: str, paused: bool) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if paused:
                raise ActionError('conflict', 'Ручная пауза истории удалена; используйте обновление фида')
            try:
                with transaction(current.db):
                    result = self.subscriptions.pause_history(identity, False)
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('invalid', str(error)) from None
            self._sync_sources()
            self.jobs.refresh_feed(identity)
            return result


    async def change_history(self, identity: str, selection: InitialHistory | None) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            try:
                with transaction(current.db):
                    result = self.subscriptions.change_history(identity, selection)
                    if selection is None:self.jobs.cancel_history(identity)
                    self.jobs.invalidate()
            except LookupError as error:
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                raise ActionError('conflict', str(error), expected=True) from None
            await self.jobs.cancel(invalidate=False)
            self._sync_sources()
            self.jobs.enqueue_discovery(identity)
            return result


    async def rename_folder(self, identity: str, title: str) -> dict[str, Any]:
        async with self._actions:
            try:
                with transaction(self.require_store().db):
                    return self.subscriptions.rename_folder(identity, title)
            except LookupError as error:
                raise ActionError('missing', str(error), expected=True) from None
            except FolderConflict as error:
                raise ActionError('conflict', str(error), code=error.code, expected=True) from None


    async def remove_folder(self, identity: str) -> None:
        async with self._actions:
            try:
                with transaction(self.require_store().db):
                    self.subscriptions.remove_folder(identity)
            except LookupError as error:
                raise ActionError('missing', str(error), expected=True) from None
            except FolderConflict as error:
                raise ActionError('conflict', str(error), code=error.code, expected=True) from None


    async def patch_feed(self, identity: str, body: FeedPatch) -> dict[str, Any]:
        async with self._actions:
            current = self.require_store()
            try:
                with transaction(current.db):
                    result, changed = self.subscriptions.patch_feed(identity, body)
                    if changed:
                        self.jobs.invalidate({identity})
            except LookupError as error:
                record_caught('runtime.patch_feed')
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                record_caught('runtime.patch_feed')
                raise ActionError('invalid', str(error)) from None
            if changed:
                await self.jobs.cancel({identity}, invalidate=False)
            if 'transport' in body.model_fields_set:
                self._sync_sources()
            return result


    async def add_feed(self, body: AddFeed) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            try:
                current = self.require_store()
                if not self._live():
                    raise ActionError('conflict', 'Откройте читатель для добавления фида', expected=True)
                url = normalized(body.url)
                if self._feed_checks.get(body.verification) != url:
                    raise ActionError('invalid', 'Сначала проверьте текущий URL фида', expected=True, code='verification_expired')
                with transaction(current.db):
                    return self.subscriptions.add_feed(body.model_copy(update={'url': url}))
            except ValueError as error:
                record_caught('runtime.add_feed')
                raise ActionError('invalid', str(error)) from error


    async def verify_feed(self, body: FeedURL) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для проверки фида')
            check = _ExternalCheck(self.execution, current)
            try:
                url = normalized(body.url)
                _, kind, _ = identify(url)
                if kind not in ('rss', 'youtube'):
                    raise ValueError('Для Telegram используйте подключение Telegram-источника')
                diagnostic_source.set('Проверка фида · ' + url)
                existing = self.subscriptions.existing_feed(url)
            except (ValueError, SourceError) as error:
                raise ActionError('invalid', error.label if isinstance(error, SourceError) else str(error)) from error
            async def download_title() -> str:
                async with asyncio.timeout(25):
                    raw, _, _ = await RSSSource(url, 1, self._rss_transport).download(url)
                    return feed_title(raw)
            pending = check.start(download_title()) if not existing else None
        try:
            title = existing['title'] if existing else await pending if pending else ''
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller and caller.cancelling():
                raise
            raise ActionError('conflict', 'Проверка фида прервана; повторите действие', expected=True) from None
        except (SourceError, TimeoutError, httpx.InvalidURL) as error:
            raise ActionError('invalid', error.label if isinstance(error, SourceError) else 'Неверный URL фида' if isinstance(error, httpx.InvalidURL) else 'Проверка фида не завершилась за 25 секунд', code='source_unavailable' if isinstance(error, (SourceError, TimeoutError)) else None) from error
        async with check.accept() as current:
            verification = secrets.token_urlsafe(24)
            self._feed_checks[verification] = url
            if len(self._feed_checks) > 100:
                del self._feed_checks[next(iter(self._feed_checks))]
            return dict(url=url, title=title, kind=kind, verification=verification,
                        existing=self.subscriptions.existing_feed(url))


    async def update_settings(self, scope: str, owner: str, body: Overrides) -> dict[str, Any]:
        async with self._actions:
            try:
                current = self.require_store()
                switch_only = bool(body.model_fields_set) and body.model_fields_set <= {'filter_enabled', 'theses'}
                with transaction(current.db):
                    change = self.subscriptions.update_settings(scope, owner, body)
                    if change.changed and not switch_only:
                        self.jobs.invalidate(change.affected)
                    current.fault('settings_after_jobs')
                self._interval_changed(change.interval)
                if change.changed and not switch_only:
                    await self.jobs.cancel(change.affected, invalidate=False)
                return change.value
            except LookupError as error:
                record_caught('runtime.update_settings')
                raise ActionError('missing', str(error)) from None
            except ValueError as error:
                record_caught('runtime.update_settings')
                raise ActionError('invalid', str(error)) from None


    async def configure_processor(self, config: ProcessorConfig) -> None:
        async with self._actions:
            with transaction(self.require_store().db):
                changed = self.connections.configure_processor(config)
                if changed:
                    self.jobs.invalidate()
            if changed:
                await self.jobs.cancel(invalidate=False)


    async def check_processor(self) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            config = self.queries.processor_config()
            if not config:
                raise ActionError('conflict', 'llama.cpp не настроен')
            check = _ExternalCheck(self.execution, current)
            pending = check.start(LlamaCpp(config, self._ai_transport).check())
        try:
            result = await pending
        except ProcessingError as error:
            record_caught('runtime.check_processor')
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Проверка прервана', expected=True) from None
        async with check.accept():
            return result


    def codex_status(self) -> dict[str, Any]:
        self.require_store()
        config = self.connections.codex_config()
        return dict(self._codex_status, config=config.model_dump() if config else None)


    async def codex_models(self) -> list[dict[str, Any]]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            check = _ExternalCheck(self.execution, current)
            pending = check.start(self._codex_client.catalog())
        try:
            result = await pending
        except ProcessingError as error:
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Загрузка каталога прервана', expected=True) from None
        async with check.accept():
            return result


    async def configure_codex(self, config: CodexSettings) -> None:
        # Validate against fresh catalog outside the action lock. Saving cannot
        # stop the in-flight generation or increase any freshness generation.
        current = self.require_store()
        models = await self.codex_models()
        try:
            config.validate_catalog(models)
        except ProcessingError as error:
            raise ActionError('invalid', error.label) from None
        async with self._actions:
            if self._store is not current or not self._live():
                raise ActionError('conflict', 'Сохранение настроек прервано', expected=True)
            with transaction(current.db):
                self.connections.configure_codex(config)


    async def check_codex(self) -> dict[str, Any]:
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения')
            check = _ExternalCheck(self.execution, current)
            pending = check.start(self._codex_client.check())
        try:
            result = await pending
        except asyncio.CancelledError:
            raise ActionError('conflict', 'Проверка Codex прервана', expected=True) from None
        async with check.accept():
            self._codex_status = result
            if self.jobs.metrics:self.jobs.metrics.observe_codex(bool(result.get('available')))
        return result


    async def retry_filter(self, item: str) -> None:
        async with self._actions:
            self.require_work()
            with transaction(self.require_store().db):
                if not self.jobs.retry_filter(item):
                    raise ActionError('missing', 'Фильтрация не включена')
                self.jobs.invalidate()
            await self.jobs.cancel(invalidate=False)


    def processing_wait(self, item: str, kind: Literal['filter', 'theses']) -> 'ProcessingWait | None':
        return self.require_store().processing.waiting(item, kind, self._clock(), self._wall_clock())


    async def retry_theses(self, item: str) -> None:
        async with self._actions:
            self.require_work()
            with transaction(self.require_store().db):
                if not self.jobs.retry_theses(item):
                    raise ActionError('missing', 'Тезисы для материала не включены')


    async def regenerate(self, item: str) -> None:
        async with self._actions:
            self.require_work()
            with transaction(self.require_store().db):
                if not self.jobs.retry_theses(item):
                    raise ActionError('missing', 'Результат недоступен для пересоздания')


    async def telegram_profiles(self) -> dict[str, Any]:
        current = self.require_store()
        accounts = (await self._call_telegram(lambda client: client.accounts()))[0] if self._telegram_client and self._live() else []
        from oko.reader.api_v3 import timestamp
        result: dict[str, Any] = dict(configurations=[dict(configuration_ref=a, label=a) for a in accounts])
        for role in ('personal', 'collection'):
            profile = self.connections.profile(role)
            if not profile:
                result[role] = dict(role=role, state='unconfigured')
                continue
            value = dict(role=role, state=profile['state'], configuration_ref=profile['account'],
                         verified_user_id=str(profile['user_id']), checked_at=timestamp(profile['checked']).root)
            if profile['state'] != 'ready':
                value.update(reason='account_identity_mismatch' if profile['state'] == 'identity_mismatch' else 'profile_unavailable', message=profile['message'] or 'Профиль недоступен')
            result[role] = value
        return result


    async def save_telegram_profile(self, role: str, account: str) -> dict[str, Any]:
        from oko.subscriptions.telegram import TelegramConflict
        async with self._actions:
            self.require_work()
            current, _ = self._telegram_ready()
            old = self.connections.profile(role)
            generation = old['generation'] if old else 0
        try:
            user_id, check = await self._call_telegram(lambda client: client.identity(account))
        except ActionError:
            raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Не удалось проверить существующую сессию', 503) from None
        if type(user_id) is not int or user_id <= 0:
            raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Клиент не подтвердил user ID', 503)
        async with check.accept() as current:
            with transaction(current.db):
                self.connections.save_profile(role, account, user_id, generation, self._wall_clock())
                self.jobs.invalidate()
            await self.jobs.cancel(invalidate=False)
            self._sources.clear()
            self._sync_sources()
        result = (await self.telegram_profiles())[role]
        assert isinstance(result, dict)
        return result


    async def _verify_telegram_profile(self, role: str) -> dict[str, Any]:
        from oko.subscriptions.telegram import TelegramConflict
        current = self.require_store()
        profile = self.connections.profile(role)
        if not profile:
            raise TelegramConflict('profile_not_configured', dict(role=role), 'Профиль Telegram не настроен')
        verification = _ExternalCheck(self.execution, current)
        try:
            user_id, check = await self._call_telegram(lambda client: client.identity(profile['account']))
        except ActionError as error:
            async with verification.accept(failure=error) as current:
                with transaction(current.db):
                    self.connections.checked(role, profile['generation'], None, self._wall_clock())
            raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Сессия Telegram недоступна', 503) from None
        async with check.accept():
            with transaction(current.db):
                valid = self.connections.checked(role, profile['generation'], user_id, self._wall_clock())
            if not valid:
                raise TelegramConflict('account_identity_mismatch', dict(role=role), 'Личность аккаунта изменилась')
            if isinstance(self._telegram_client, LocalTelegram):
                self._telegram_client.expected_users[profile['account']] = user_id
        return profile


    async def connect_telegram_binding(self, source: str, folder: str, history: InitialHistory | None, role: str | None, auto_read: bool | None = None, feed_id: str | None = None) -> dict[str, Any]:
        from oko.subscriptions.telegram import locator, TelegramConflict
        kind, address = locator(source)
        async with self._actions:
            self.require_work()
            current = self.require_store()
            if not self._live():
                raise ActionError('conflict', 'Откройте читатель для подключения', expected=True)
            duplicates = self.connections.owners([(kind, str(address))])
            if any(identity!=feed_id for identity in duplicates):
                raise TelegramConflict('duplicate_source', self.connections.duplicates(duplicates), 'Источник уже подключён')
            previous=self.queries.binding(feed_id) if feed_id else None
            if feed_id and not previous:
                raise TelegramConflict('access_immutable',dict(feed_id=feed_id),'Фид не найден или не имеет привязки Telegram')
            if feed_id and not self.connections.supports_access_change(feed_id):
                raise TelegramConflict('access_immutable',dict(feed_id=feed_id),'Этот сохранённый фид ещё не поддерживает смену доступа')
            connection = _ExternalCheck(self.execution, current)
        profile = await self._verify_telegram_profile(role) if role else None
        if profile:
            info, check = await self._call_telegram(lambda client: client.info(profile['account'], '@' + str(address) if kind == 'username' else address))
            connection = connection.follow(check)
            if info.get('kind')!='channel' or info.get('forum',False):
                raise TelegramConflict('source_connection_invalid',dict(reason='unsupported_group'),
                    'Группы Telegram пока не поддерживаются; подключите канал',400)
            binding = dict(kind='client', role=role, account_user_id=str(profile['user_id']), peer_id=str(info['peer']))
        else:
            if kind != 'username':
                raise ValueError('Веб-доступ требует публичный username')
            from oko.collection.telegram.web import preview
            async with connection.accept(require_work=True):
                pending = connection.start(preview(str(address), self._telegram_http))
            try:
                info = await pending
            except SourceError as error:
                raise TelegramConflict('source_unavailable', dict(retry_at=None), error.label, 503) from None
            binding = dict(kind='web', username=info['username'], preview_url=info['preview_url'])
            if previous and previous['kind']=='client':
                old_profile=await self._verify_telegram_profile(previous['role'])
                verified,check=await self._call_telegram(lambda client:client.info(old_profile['account'],int(previous['peer_id'])))
                connection=connection.follow(check)
                aliases={name.lower() for name in [verified.get('username'),*verified.get('usernames',[])] if isinstance(name,str)}
                if info['username'].lower() not in aliases:
                    raise TelegramConflict('source_identity_conflict',dict(feed_id=feed_id),'Публичный адрес прежнего канала не подтверждён')
                info.update(peer=verified['peer'],kind=verified.get('kind'))
        async with connection.accept() as current:
            if profile:
                latest = self.connections.profile(str(role))
                if not latest or latest['generation'] != profile['generation']:
                    raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Профиль изменился во время подключения', 503)
            if feed_id:
                if self.queries.binding(feed_id)!=previous:
                    raise TelegramConflict('source_identity_conflict',dict(feed_id=feed_id),'Привязка изменилась во время проверки; повторите действие')
                try:
                    with transaction(current.db):
                        result=self.connections.change_access(feed_id,binding,info,auto_read)
                        self.jobs.change_adapter(feed_id)
                        self.jobs.invalidate({feed_id})
                except INTEGRITY_ERRORS:
                    raise TelegramConflict('source_identity_conflict',dict(feed_id=feed_id),'Внешний ключ уже принадлежит другому фиду') from None
                await self.jobs.cancel({feed_id}, invalidate=False)
                self._sources.pop(feed_id,None)
            else:
                with transaction(current.db):
                    result = self.connections.connect(binding, info, source, folder, history, bool(auto_read))
            self._sync_sources()
            return result


    def _telegram_ready(self) -> tuple[Store, TelegramClient]:
        current = self.require_store()
        if not self._live():
            raise ActionError('conflict', 'Откройте читатель для подключения')
        if not self._telegram_client:
            raise ActionError('conflict', 'Telegram-клиент не настроен')
        return current, self._telegram_client


    async def _call_telegram(self, call: Callable[[TelegramClient], Awaitable[T]]) -> tuple[T, _ExternalCheck]:
        async with self._actions:
            self.require_work()
            current, client = self._telegram_ready()
            check = _ExternalCheck(self.execution, current)
            pending = check.start(call(client))
        try:
            # Network work is deliberately outside the mutation seam.
            result = await pending
        except SourceError as error:
            record_caught('runtime._call_telegram')
            raise ActionError('conflict', error.label) from None
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller and caller.cancelling():
                raise
            raise ActionError('conflict', 'Око остановлено' if self.stopped else 'Подключение Telegram прервано; повторите действие', expected=True) from None
        async with check.accept():
            return result, check


    async def telegram_accounts(self) -> list[str]:
        result, _ = await self._call_telegram(lambda client: client.accounts())
        return result


    async def remove_feed(self, identity: str) -> None:
        async with self._actions:
            current = self.require_store()
            members = self.queries.feed_members(identity)
            with transaction(current.db):
                self.subscriptions.remove_feed(identity)
                self.jobs.remove_feed(identity,members)
                self.jobs.invalidate({identity})
            if self.diagnostics:
                self.diagnostics.forget_materials(members)
            await self.jobs.cancel({identity}, invalidate=False)
            self._sources.pop(identity, None)
            self._telegram_bindings.pop(identity, None)


    def _clean_collection(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.diagnostics.clean(value) if self.diagnostics else value
        if isinstance(value, list):
            return [self._clean_collection(item) for item in value]
        if isinstance(value, dict):
            result = {key: self._clean_collection(item) for key, item in value.items()}
            if 'url' in value and result['url'] != value['url']:
                result['redacted'] = True
            return result
        return value
