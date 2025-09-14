ALTER TABLE children ADD COLUMN phone VARCHAR;
CREATE INDEX IF NOT EXISTS ix_children_phone ON children(phone);