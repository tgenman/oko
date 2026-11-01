"""Focused read-problem projection and bounded-work checks."""
import json
import pytest
from oko.storage.read_job_problems import candidates
from tests.support.read_problem import setup
from tests.support.storage import Store


def test_normal_wait_is_not_a_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','waiting') RETURNING id").fetchone()[0]
        assert candidates(store,job_id,10)==[]
    finally:store.db.close()
