-- SettingsRepository advances the source revision in the owning transaction.
DROP TRIGGER source_configuration_settings_delete ON settings;
DROP TRIGGER source_configuration_settings_insert ON settings;
DROP TRIGGER source_configuration_settings_update ON settings;
DROP FUNCTION oko_trigger_source_configuration_settings_delete();
DROP FUNCTION oko_trigger_source_configuration_settings_insert();
DROP FUNCTION oko_trigger_source_configuration_settings_update();
