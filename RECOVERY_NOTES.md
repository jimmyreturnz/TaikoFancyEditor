# Recovery and localization stabilization

Recovered from the supplied Repomix snapshot and patched directly.

## Restored Settings integration
- Restored the Settings button to the top toolbar.
- Restored SettingsDialog, SettingsManager, and ShortcutRegistry integration.
- Preserved language persistence and the restart-required prompt.
- Configured Play/Pause, Undo, and Redo shortcuts from saved settings.
- Settings changes reload shortcuts without restarting.
- Text-entry controls retain normal editing shortcuts.

## Localization stabilization
- Installs the configured Qt translator before MainWindow construction.
- Uses explicit contexts for MainWindow, Parameters, Transformations, and DrawingDialog.
- Translates dynamic parameter labels and visible choice labels at display time.
- Keeps stable internal transformation IDs, parameter keys, and choice values untranslated.
- Uses stable all and split item data rather than translated display text for mode logic.
- Translates Play/Pause and timeline labels including Duration, Now, and Snap.

## Narrow regression fixes
- Removed the duplicate toolbar status widget.
- Corrected the Drawing preview path from nonexistent status_label to status.
- Wheel handling, transformation algorithms, map writing, and image tracing were not redesigned.

## Validation
- Python syntax compilation was run across the recovered repository.
- Structural invariants were checked for Settings wiring, translator startup order, stable mode data, one status widget, and absence of the invalid status_label reference.
