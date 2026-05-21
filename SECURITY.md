# Security

Do not commit live credentials, token files, local `.env` files, SQLite state,
or audit logs. The root `.gitignore` is intentionally broad so local deployment
state stays out of source control.

## Credential Handling

- Store broker tokens, Discord tokens, and Infisical machine identity files outside the repo.
- Keep token files mode `0600` where possible.
- Use the checked-in `.env.example` files only as templates.
- Rotate any token that was copied into logs, shell history, chat, or a local
  file that might have been shared.

## Known Pre-Publish Rotation

Before the first public publish, this working tree contained local Discord and
secret-provider credentials in ignored files. Those files are removed from the
repository contents, but the credentials should still be rotated before reuse.

## Reporting

For private security issues, contact the repository maintainers directly rather
than opening a public issue with secret material.
