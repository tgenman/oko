"""Request-scoped SQL/PostgreSQL-plan/publication-file/JSON measurements."""
from contextlib import ExitStack
from collections import Counter
import json
from pathlib import Path
import time
from unittest.mock import patch

from tests.support.reading_cost import request_sql,tracing
from tests.support.postgres_work import measure_reads


class CountedFile:
    def __init__(self,file,record):
        self.file,self.record=file,record
    def __getattr__(self,name):
        return getattr(self.file,name)
    def __enter__(self):
        self.file.__enter__()
        return self
    def __exit__(self,*args):
        return self.file.__exit__(*args)
    def read(self,*args):
        value=self.file.read(*args);self.record(value);return value
    def readline(self,*args):
        value=self.file.readline(*args);self.record(value);return value
    def readlines(self,*args):
        values=self.file.readlines(*args)
        for value in values:self.record(value)
        return values
    def __iter__(self):
        return self
    def __next__(self):
        value=next(self.file);self.record(value);return value


class ReadingMeasurements:
    def __init__(self,client,app,root):
        self.client,self.app,self.root=client,app,root
        self.counts=Counter()
        self.files=Counter()
        self.stack=ExitStack()

    def __enter__(self):
        tracing(self.client,self.app)
        original_open,original_stat,loads,dumps=Path.open,Path.stat,json.loads,json.dumps
        # Keep the existing report keys; file totals include current media and
        # legacy content paths so either kind of unexpected I/O is visible.
        directories=(self.root/'media',self.root/'content')
        def publication_file(path):
            return any(path.is_relative_to(directory) for directory in directories)
        def record(value):
            if request_sql.get() is not None:
                self.counts['read_bytes']+=len(value.encode() if isinstance(value,str) else value)
        def open_file(path,*args,**kwargs):
            file=original_open(path,*args,**kwargs)
            mode=kwargs.get('mode',args[0] if args else 'r')
            if request_sql.get() is not None and publication_file(path) and mode.startswith('r') and '+' not in mode:
                self.counts['content_opens']+=1;self.files[str(path)]+=1
                return CountedFile(file,record)
            return file
        def stat(path,*args,**kwargs):
            if request_sql.get() is not None and publication_file(path):
                self.counts['content_stats']+=1
            return original_stat(path,*args,**kwargs)
        def load(value,*args,**kwargs):
            if request_sql.get() is not None:
                self.counts['json_loads']+=1
                self.counts['json_input_bytes']+=len(value.encode() if isinstance(value,str) else value)
            return loads(value,*args,**kwargs)
        def dump(value,*args,**kwargs):
            result=dumps(value,*args,**kwargs)
            if request_sql.get() is not None:
                self.counts['json_dumps']+=1;self.counts['json_output_bytes']+=len(result.encode())
            return result
        self.stack.enter_context(patch.object(Path,'open',open_file))
        self.stack.enter_context(patch.object(Path,'stat',stat))
        self.stack.enter_context(patch.object(json,'loads',load))
        self.stack.enter_context(patch.object(json,'dumps',dump))
        self.work=self.stack.enter_context(measure_reads(self.app.state.host.require_store().db,
            enabled=lambda:request_sql.get() is not None))
        return self

    def request(self,name,method,url,**options):
        self.counts.clear();self.files.clear()
        self.work.rows=self.work.buffers=self.work.reads=self.work.writes=0
        start=time.perf_counter()
        response=self.client.request(method,url,**options)
        elapsed=time.perf_counter()-start
        assert response.status_code==200,response.text
        result=dict(name=name,sql=int(response.headers['X-Test-SQL']),rows_scanned=self.work.rows,buffers=self.work.buffers,
            content_opens=self.counts['content_opens'],content_stats=self.counts['content_stats'],read_bytes=self.counts['read_bytes'],
            json_loads=self.counts['json_loads'],json_input_bytes=self.counts['json_input_bytes'],
            json_dumps=self.counts['json_dumps'],json_output_bytes=self.counts['json_output_bytes'],
            response_bytes=len(response.content),ms=round(elapsed*1000,3))
        return response,result

    def __exit__(self,*args):
        self.stack.close()
