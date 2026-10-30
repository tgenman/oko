DROP INDEX reader_problem_expiry;
CREATE INDEX reader_problem_expiry ON reader_problems(finished,id) WHERE finished IS NOT NULL;
ALTER TABLE reader_problems DROP COLUMN rowid;
