# Security Policy

TraitTutor is currently pre-1.0 and should be treated as an actively evolving learning product.

## Supported version

Security fixes target the `main` branch.

## Reporting a vulnerability

Please report security issues privately to the repository maintainer. Do not open a public issue with credentials, tokens, exploit details, private deployment URLs, or user data.

Useful information to include:

- affected commit or release;
- deployment mode;
- reproduction steps;
- whether authentication is enabled;
- expected impact.

## Security expectations

- Keep registration invite-only unless the deployment owner explicitly changes it.
- Do not commit `.env`, runtime settings with real keys, generated databases, uploaded materials, or model credentials.
- File uploads must preserve filename sanitization, content-type checks, and size limits.
- Generated artifacts and downloads must remain behind the authenticated output gateway.
- Model calls should go through the configured gateway so prompts and usage are auditable.
