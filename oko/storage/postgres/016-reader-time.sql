-- Reader freshness derives from the actual deadlines and live runtime metadata.
CREATE INDEX jobs_reader_deadlines ON jobs(next)
    WHERE status='pending' AND kind IN ('fetch','filter','theses');
DROP TRIGGER reader_v_job_time_insert ON jobs;
DROP TRIGGER reader_v_job_time_update ON jobs;
DROP TRIGGER reader_v_job_time_delete ON jobs;
DROP TRIGGER reader_v_time_ready ON reader_job_time;
DROP TRIGGER reader_v_youtube_time ON reader_youtube_time;
DROP TRIGGER reader_v_runtime ON reader_runtime;
DROP TRIGGER reader_v_runtime_first ON reader_runtime;
DROP FUNCTION oko_trigger_reader_v_job_time_insert();
DROP FUNCTION oko_trigger_reader_v_job_time_update();
DROP FUNCTION oko_trigger_reader_v_job_time_delete();
DROP FUNCTION oko_trigger_reader_v_time_ready();
DROP FUNCTION oko_trigger_reader_v_youtube_time();
DROP FUNCTION oko_trigger_reader_v_runtime();
DROP FUNCTION oko_trigger_reader_v_runtime_first();
DROP TABLE reader_job_time;
DROP TABLE reader_youtube_time;
DROP TABLE reader_runtime;
