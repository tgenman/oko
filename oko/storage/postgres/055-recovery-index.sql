-- Startup recovery walks unviewed ids without traversing a viewed archive.
CREATE INDEX items_unviewed_id ON items(id) WHERE viewed=0;
