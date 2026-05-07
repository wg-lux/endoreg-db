# lx-ai-core

Standalone AI runtime and deployment core for the LX ecosystem.

This package intentionally contains no Django models, no Celery tasks, no direct
database access, and no network transfer logic. Integrating callers such as
`endoreg-db` own storage, provenance persistence, queueing, and security policy.
