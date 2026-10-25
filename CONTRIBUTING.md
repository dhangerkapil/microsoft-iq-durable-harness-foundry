# Contributing

Contributions are welcome: bug fixes, clearer docs, new story scenarios, better evaluations.

1. Open an issue first for anything larger than a small fix.
2. Keep the story fictional: no real people, clients, tenants or data.
3. Run the checks before a pull request:

   ```powershell
   python -m pip install --pre -e ".[dev,ui]"; python -m pytest -q
   .\scripts\Test-StoryDates.ps1
   npm --prefix ui ci; npm --prefix ui test
   terraform fmt -check -recursive terraform
   pwsh -NoProfile -File scripts\Test-DemoDocs.ps1
   ```

   Without local Python packages, `.\scripts\Test-AgentInAcr.ps1 -Tests tests` runs the tests in Azure Container
   Registry (`-Ui` runs the app's checks there).
4. Agent behavior changes go in `.agent_configs/team/baseline` (instructions, skills, model). Show the evaluation
   before and after ([docs/QUALITY-LOOP.md](docs/QUALITY-LOOP.md)) in the pull request.
5. A new or changed demo prompt goes in `demo/expectations.yaml` and `docs/DEMO-SCRIPT.md` together; the docs check
   fails when they differ.
