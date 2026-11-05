-- A rebuild has its own durable generation and never overwrites a fetch Job.
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_allowed;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_allowed CHECK (kind IN
 ('discover','fetch','rebuild','download_media','filter','theses','read','delete','feed-delete'));
