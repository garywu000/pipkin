# Security

Report a suspected vulnerability through [GitHub private vulnerability reporting](https://github.com/garywu000/pipkin/security/advisories/new). Use a synthetic workspace in reproductions and redact paths, credentials, and document contents.

The built-in file tools validate paths within a selected workspace. Agent command approvals give you a chance to review a command before execution. Approved commands execute with the current macOS user's permissions. Review access to other files and network destinations before approving them.

Use a downloaded local model for on-device inference. The browser, search, and web-fetch tools can send requests to external sites. Check the active workspace and permission mode before asking Agent to work with private files.
