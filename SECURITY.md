# Security Policy

Routenda is Alpha software. Do not deploy it as-is with real supplier or employee data until you have added production identity, authorization, audit retention, backup, monitoring, and provider-specific data controls.

## Reporting

Open a private security advisory on GitHub when available, or contact the maintainers privately before publishing details. Include reproduction steps, affected versions, and the expected impact.

## Secrets

Never commit `.env`, `.env.local`, provider exports, tokens, API keys, supplier contact lists, or calendar data. Rotate any credential that has been shared outside a trusted secret manager.

## Current Boundaries

- Demo role checks use `X-Role`; production deployments must put Routenda behind a trusted identity provider and inject roles server-side.
- Public availability links are random, hashed at rest, expiring, revocable, and single-use.
- Health endpoints report configured adapter types but never return secret values.
