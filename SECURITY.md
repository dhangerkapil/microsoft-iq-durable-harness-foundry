# Security

This repository is a sample. It deploys into a Microsoft 365 tenant and an Azure subscription that you control and
works only with fictional data. Use a demo or developer tenant.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through GitHub: **Security >
Report a vulnerability** on this repository. Include the steps to reproduce and the affected files.

## How the sample handles data and secrets

- The agents call Work IQ and Fabric **on behalf of the signed-in user**, after that user's consent, so they can read
  only what the user can read. They never use a shared user token.
- Writes are limited: the team can create a draft email only after the user approves it, and the Work IQ tenant
  policy blocks updates and deletes. Review-pack drafts are previewed, never saved.
- No secrets are stored in the repository. `.env.local` and `terraform/*.tfvars` are git-ignored and hold settings,
  not passwords. Client secrets for the Work IQ and Fabric IQ connections are created at deployment time and stored
  only in the Foundry connections; the Web IQ key is sent only to Azure Resource Manager and stored in its connection.
- Traces can include prompts and tool payloads when `agent_capture_content` is on (the deployment script's default for
  synthetic demo tenants). Turn it off for anything but synthetic data.
