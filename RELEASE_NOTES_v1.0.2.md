# Taiko Fancy Arranger v1.0.2

Version 1.0.2 is a security-hardening and stability update.

## Security hardening

- Restricted beatmap audio and background paths to the selected song folder.
- Added allowlists for supported audio and image extensions.
- Added equation length, syntax, nesting, exponent, and numeric-result limits.
- Restricted exported files to the `.osu` extension.
- Added validation for Approach Rate and Circle Size.
- Added validated atomic `.osu` export writing.
- Added security regression tests.
- Added SHA-256 checksum generation to the Windows build workflow.
- Reviewed the source for process execution, persistence, hidden networking, downloads, registry modification, credential access, generated executables, and generated DLL behavior.

No source behavior associated with malware installation, persistence, credential theft, remote command execution, or hidden network access was identified during the source review.

## Fixes

- Fixed an undefined wheel-delta variable in timeline scrolling.
- Prevented long beatmap metadata from expanding the application beyond the monitor resolution.
- Long metadata is now shortened visually while the full value remains available through its tooltip.
- Preserved Drawing, Text font selection, Equation, playback, timing bar, bookmarks, PreviewTime, AR/CS controls, and transformation behavior.

## Release verification

Before publishing the Windows binary:

- Run the complete test suite.
- Build from the tagged source.
- Scan the unpacked application folder.
- Scan the final ZIP.
- Verify the generated SHA-256 checksums.
- Submit any unexpected Microsoft Defender result for analysis.

## Windows notice

The application is currently unsigned. Windows SmartScreen or Microsoft Defender might apply reputation-based or machine-learning classifications to newly compiled binaries. Do not disable security software or add permanent exclusions to run the application.