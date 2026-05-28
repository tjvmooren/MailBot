# MailBot

MailBot is a local Python CLI for reviewing Gmail messages with an LLM-assisted safety layer. It authenticates with Gmail OAuth desktop credentials, reads recent messages, classifies them through a provider abstraction, stores local scan sessions in SQLite, and only allows one write action in v1: moving user-approved safe candidates to Gmail trash.

MailBot does not send email, draft replies, or permanently delete messages.

## Features

- `auth` to complete Gmail desktop OAuth locally.
- `unread --limit N` to review recent unread messages.
- `important --limit N` to review important messages.
- `cleanup --limit N` to find likely cleanup candidates and store numbered IDs.
- `search "gmail query"` to run any Gmail search query and store numbered IDs.
- `trash --ids 1,2,3` to move approved safe candidates from the latest `cleanup` or `search` session to trash.
- SQLite-backed session mapping so display IDs remain stable for the latest actionable scan.
- LLM provider abstraction with OpenAI first, designed so Ollama or Hugging Face can be added later.

## Safety Rules

MailBot blocks trash recommendations for:

- job or employer messages
- school or professor messages
- bank or finance messages
- government or legal messages
- security or account alerts
- personal human emails
- any email with attachments
- emails that mention deadlines, interviews, offers, or action-required language

Even if the model suggests trash, the safety layer downgrades protected messages away from trash and `trash --ids ...` refuses to move blocked items.

## Setup

1. Create and activate a virtual environment.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install the project.

```powershell
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

3. Create `.env` from `.env.example` and set `OPENAI_API_KEY`.

```powershell
Copy-Item .env.example .env
```

4. Create Gmail desktop OAuth credentials.

- Open the Google Cloud console.
- Enable the Gmail API for your project.
- Configure the OAuth consent screen if prompted.
- Create an OAuth client with application type `Desktop app`.
- Download the credential JSON and save it as `credentials.json` in the project root.

5. Authorize MailBot with Gmail.

```powershell
mailbot auth
```

This creates `token.json` in the project root. If you later change Gmail scopes, re-run `mailbot auth` and refresh the token.

## Commands

```powershell
mailbot auth
mailbot unread --limit 10
mailbot important --limit 10
mailbot cleanup --limit 15
mailbot search "from:linkedin.com is:unread"
mailbot trash --ids 1,2,3
```

`cleanup` and `search` save the latest actionable result set to `data/mailbot.db`. The `trash` command only operates on message IDs from the most recent actionable session and asks for confirmation before moving messages to Gmail trash.

## Configuration

Provider and model settings live in `config.yaml`.

```yaml
provider: openai
providers:
  openai:
    model: gpt-5-mini
```

The provider interface lives under `src/mailbot/providers/`, so additional backends can be added without changing the CLI surface.

## Development

Run tests:

```powershell
python -m pytest
```
