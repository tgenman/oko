"""Independent filtering and summary crons sharing the existing model capacity."""
import asyncio
import time
from collections.abc import Callable
import httpx
from oko.execution import Execution
from oko.workers.control import JobControl
from oko.storage.store import Store
from oko.storage.job_types import Job
from oko.storage.job_repository import JobRepository, JobRejected
from oko.domain.processing import Kind, ProcessingInput
from oko.services.processing import ProcessingService, StaleInput
from oko.processing.codex import Codex, CodexClient
from oko.processing.llamacpp import LlamaCpp, GENERATION_PARAMETERS
from oko.processing.execution import CodexChoice
from oko.processing.contracts import ProcessingError
from oko.diagnostics import record_caught, diagnostic_materials, diagnostic_source


class ProcessingWorker:
    def __init__(self, kind: Kind, execution: Execution, tasks: JobControl, clock: Callable[[], float], codex: CodexClient,
                 transport: httpx.AsyncBaseTransport | None) -> None:
        self.kind,self.execution,self.tasks,self.clock,self.codex,self.transport=kind,execution,tasks,clock,codex,transport
        self.service=ProcessingService(execution.require_store)

    async def run(self) -> None:
        while not self.execution.stopped:
            active = False
            try:
                active = await self.tick()
            except Exception:
                record_caught('Крон '+self.kind)
            await asyncio.sleep(.02 if active else .1)

    async def tick(self) -> bool:
        async with self.execution._actions:
            store=self.execution._store
            if store is None or not self.execution._live() or self.tasks._cancelling:
                return False
            jobs=JobRepository(store)
            if not store.processing.background:
                with jobs.transaction(self.execution._live):
                    if not self.execution._live():
                        return False
                    self.service.reconcile()
            if self.tasks._ai and self.tasks._ai.done():
                if not self.tasks._ai.cancelled():
                    await self.tasks._ai
                self.tasks._ai=None
            if self.tasks._ai or self.tasks._detached_ai:
                return True
            if jobs.deleting():
                return False
            with jobs.transaction(self.execution._live):
                if not self.execution._live():
                    return False
                job=jobs.claim_processing(self.kind,self.clock())
                if job is None:
                    return False
                prepared=self.service.prepare(job.item,self.kind)
                work=prepared.input
                if work is None:
                    if prepared.reason == 'stage_disabled':
                        jobs.block(job)
                    else:
                        jobs.cancel(job)
                    return True
                # Snapshot and successful claim are committed together.
                jobs.save_input(job, work)
                request=work.request.model_copy(update={'generation':job.generation})
                origin=jobs.origin(job,work.signature)
                if origin is None or request.processor!='codex':
                    if request.processor=='codex':
                        codex_config=self.service.codex_config()
                        origin=dict(processor='codex',**codex_config.choice(request.operation).model_dump()) if codex_config else None
                    else:
                        local_config=self.service.processor_config()
                        origin=dict(processor=request.processor,model=local_config.model if local_config else None,parameters=dict(GENERATION_PARAMETERS))
                    if origin:
                        jobs.save_origin(job,work.signature,origin)
                if origin and origin['processor']=='codex':
                    request=request.model_copy(update={'codex_choice':CodexChoice.model_validate({k:origin[k] for k in ('model','reasoning_effort','fast')})})
                work=ProcessingInput(work.item,work.kind,work.signature,work.revision,request,work.public_ai,work.no_input)
                self.tasks._ai_feed=self.service.feed(job.item)
            self.tasks._ai=asyncio.create_task(self.execute_attempt(store,job,work,origin))
            return True

    def current(self, store: Store, jobs: JobRepository, job: Job, work: ProcessingInput) -> bool:
        return (self.execution._store is store and self.execution._live() and jobs.valid(job) and self.service.current(work))

    async def execute_attempt(self, store: Store, job: Job, work: ProcessingInput, origin: dict[str, object] | None) -> None:
        jobs=JobRepository(store)
        started, outcome = time.monotonic(), 'stale'
        observed_error: BaseException | None = None
        if self.tasks.metrics:
            from oko.observability import event
            event('job_started', job_id=job.id, kind=self.kind)
        try:
            diagnostic_materials.set(tuple(source.id for source in work.request.sources))
            diagnostic_source.set('Обработка · '+self.kind+' · '+job.item)
            result=None
            if not work.no_input:
                config=self.service.processor_config()
                adapter: Codex | LlamaCpp
                if work.request.processor=='codex':
                    if not work.public_ai:
                        raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель',code='ai-permission')
                    adapter=Codex(self.codex)
                elif config and work.request.processor in ('llamacpp','llamacpp:'+config.model):
                    adapter=LlamaCpp(config,self.transport)
                else:
                    raise ProcessingError('Ожидание выбранного обработчика; llama.cpp не настроен или не выбран',code='processor-config')
                try:
                    checked=await adapter.check()
                except Exception:
                    if isinstance(adapter,Codex) and self.tasks.metrics:self.tasks.metrics.observe_codex(False)
                    raise
                if isinstance(adapter,Codex) and self.tasks.metrics:self.tasks.metrics.observe_codex(bool(checked.get('available')))
                async with self.execution._actions:
                    if not self.current(store,jobs,job,work):
                        if self.execution._store is store and self.execution._live():
                            with jobs.transaction(self.execution._live):
                                if jobs.valid(job):jobs.cancel(job)
                        return
                    if not checked['local'] and not work.public_ai:
                        raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель',code='ai-permission')
                result=(await adapter.generate(work.request,jobs.workspace(job)) if isinstance(adapter,Codex) else await adapter.generate(work.request))
            async with self.execution._actions:
                if self.execution._store is not store or not self.execution._live():
                    return
                with jobs.transaction(self.execution._live):
                    if not self.current(store,jobs,job,work):
                        if jobs.valid(job):
                            jobs.cancel(job)
                        return
                    self.service.accept(work,result,origin)
                    jobs.checkpoint('processing_after_result')
                    jobs.finish(job)
                    self.service.refresh(job.item)
                    jobs.checkpoint('processing_after_finish')
                jobs.checkpoint('processing_after_commit')
                outcome = 'accepted'
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except (StaleInput,JobRejected):
            # Roll back the rejected result first, then cancel only this attempt.
            async with self.execution._actions:
                if self.execution._store is store and self.execution._live():
                    with jobs.transaction(self.execution._live):
                        if jobs.valid(job):jobs.cancel(job)
        except Exception as error:
            outcome, observed_error = 'error', error
            record_caught('Воркер '+self.kind)
            async with self.execution._actions:
                if self.execution._store is not store or not self.execution._live():
                    return
                with jobs.transaction(self.execution._live):
                    if self.current(store,jobs,job,work):
                        jobs.fail(job,error.label if isinstance(error,ProcessingError) else 'Ошибка AI-обработки',
                            code=error.code if isinstance(error,ProcessingError) else 'processing-failed')
                    elif jobs.valid(job):
                        jobs.cancel(job)
        finally:
            if self.tasks.metrics:
                from oko.observability import event
                elapsed = time.monotonic()-started
                self.tasks.metrics.attempt(self.kind, outcome, elapsed)
                event('job_finished', job_id=job.id, kind=self.kind, level='error' if outcome == 'error' else 'info', error=observed_error, elapsed=elapsed)


class FilterWorker(ProcessingWorker):
    def __init__(self, execution: Execution, tasks: JobControl, clock: Callable[[],float], codex: CodexClient, transport: httpx.AsyncBaseTransport | None) -> None:
        super().__init__('filter',execution,tasks,clock,codex,transport)


class SummaryWorker(ProcessingWorker):
    def __init__(self, execution: Execution, tasks: JobControl, clock: Callable[[],float], codex: CodexClient, transport: httpx.AsyncBaseTransport | None) -> None:
        super().__init__('theses',execution,tasks,clock,codex,transport)
