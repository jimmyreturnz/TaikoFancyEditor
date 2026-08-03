# Security review

## Result
No source behavior consistent with persistence, credential theft, remote command execution, hidden network access, or malware installation was found in the supplied tracked source. Wacatac is a Defender detection label on the generated archive, not a source file.

## Hardening applied
- Audio/background paths stay inside the song folder and use allowlisted media extensions.
- Working-directory/executable-directory icon probing and native `ctypes` calls were removed.
- Equation AST size, depth, exponent, result, names, calls, and syntax are bounded.
- Export is restricted to `.osu`; AR/CS and version-line inputs are validated.
- Exported maps use validated atomic replacement.
- CI produces SHA-256 checksums and review artifacts but does not auto-publish a public binary during the investigation.

## Feature effects
- Alt+wheel now depends only on Qt reporting Alt. On a mouse/driver where Qt drops the modifier, Alt+wheel may not trigger.
- Absolute or parent-traversing AudioFilename/background paths are rejected. Normal song-folder assets continue working.
- Equations over 512 characters, 24 AST levels, 128 nodes, exponent magnitude 32, or result magnitude 1e12 are rejected.
- CI builds an Actions artifact for review but no longer attaches it to a public GitHub Release.

## Important limitation
Static review cannot reproduce Defender's proprietary ML model or prove a compiled binary benign. Build from a clean tag, scan folder and ZIP, submit the exact detection to Microsoft, and sign before public distribution.
