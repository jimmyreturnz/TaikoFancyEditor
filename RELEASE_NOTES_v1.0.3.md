# Taiko Fancy Arranger v1.0.3

This release introduces the foundation for future internationalization and user customization.

## New Features

### Settings Menu
A new Settings dialog has been added, accessible through the gear icon in the toolbar.

Current settings include:

- Language selection
- Keyboard shortcut configuration
- General application preferences
- Settings management tools

### Configurable Keyboard Shortcuts
Keyboard shortcuts can now be customized through the Settings menu.

Current configurable actions:

- Play / Pause
- Undo
- Redo

Additional shortcuts may become configurable in future releases.

### Japanese Language Support
The application now includes Japanese localization support.

Users can switch between:

- English
- Japanese

Language preference is saved automatically between sessions.

### Restart Notification
Changing the application language now displays a simple restart notification to ensure the selected language is applied consistently.

## Improvements

### Localization Infrastructure
A full translation framework has been implemented to support future languages without major code changes.

This lays the groundwork for:

- Thai
- Additional community translations
- Future UI localization updates

### Persistent User Settings
Application settings are now stored and restored automatically between launches.

### UI Polish
Various interface improvements were made to support localization and future customization features.

## Technical Notes

- Existing beatmap workflows remain unchanged.
- Existing transformations remain unchanged.
- Existing project files remain fully compatible.
- No changes were made to exported beatmap formats.

## Known Limitations

- Some interface areas may still contain untranslated text.
- Language changes currently require restarting the application.
- Only English and Japanese are currently available.

## What's Next

Planned work includes:

- Text transformation rotation
- Per-character text generation
- Additional localization coverage
- Direct canvas editing improvements
- Visual selection and manipulation tools
