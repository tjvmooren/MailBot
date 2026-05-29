# MailBot

MailBot is a local Python CLI for reviewing Gmail messages with an LLM-assisted safety layer. It authenticates with Gmail OAuth desktop credentials, reads recent messages, classifies them through a provider abstraction, stores local scan sessions in SQLite, and only allows one write action in v1: moving user-approved safe candidates to Gmail trash.

MailBot does not send email, draft replies, reply to messages, or permanently delete messages.

## Features

- `auth` to complete Gmail desktop OAuth locally.
- `unread --limit N` to review recent unread messages.
- `important --limit N` to review important messages.
- `cleanup --limit N` to find likely cleanup candidates and store numbered IDs.
- `chat` to use MailBot in an interactive natural-language assistant loop.
- `voice` to use a minimal Windows voice interface that feeds speech into the same chat controller.
- `review` to re-display the latest actionable cleanup/search session from SQLite only.
- `search "gmail query"` to run any Gmail search query and store numbered IDs.
- `trash --ids 1,2,3` to move approved safe candidates from the latest `cleanup` or `search` session to trash.
- SQLite-backed session mapping so display IDs remain stable for the latest actionable scan.
- LLM provider abstraction with OpenAI first, designed so Ollama or Hugging Face can be added later.
- Direct post-trash verification by Gmail message ID to confirm the moved message now has the `TRASH` label.

## Safety Rules

MailBot blocks trash recommendations for:

- job or employer messages
- school or professor messages
- bank or finance messages
- government or legal messages
- security or account alerts
- personal human emails
- any email with attachments
- emails that contain real deadlines, interviews, applications, invoices, billing warnings, password or login issues, verification requests, legal or government notices, or signature requests

Marketing urgency by itself, such as `limited time offer` or `last chance`, does not block a clearly promotional trash recommendation.

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

MailBot uses Gmail read/modify access so it can read messages and move selected safe candidates to Gmail Trash. It does not request Gmail send or compose access.

Do not commit these local files or directories:

- `.env`
- `credentials.json`
- `token.json`
- `data/`
- `logs/`
- `.venv/`

## Commands

```powershell
mailbot auth
mailbot unread --limit 10
mailbot important --limit 10
mailbot cleanup --limit 15
mailbot chat
mailbot voice
mailbot review
mailbot review --trash-candidates
mailbot search "from:linkedin.com is:unread"
mailbot trash --ids 1,2,3
```

`cleanup` and `search` save the latest actionable result set to `data/mailbot.db`. The `review` command replays that latest actionable session from SQLite only, without calling Gmail or OpenAI again. The `trash` command only operates on message IDs from the most recent actionable session and asks for confirmation before moving messages to Gmail Trash.

`chat` wraps the same MailBot engine in an interactive assistant loop. It routes natural-language requests such as `show my unread emails`, `find recent Microsoft emails`, `review my latest cleanup results`, and `trash item 3` back into the existing safe service functions.

`voice` is a narrow Windows-only interface layer. It uses built-in Windows speech APIs for speech-to-text and text-to-speech, then sends the recognized text into the same chat controller and safety layer used by terminal chat. Voice mode still does not add any new Gmail capabilities, and trash still requires the explicit `TRASH` confirmation phrase.

For `voice` to work reliably, Windows microphone access and the local Windows speech engine must be available. If Windows speech recognition or text-to-speech is unavailable, MailBot will fall back to printing a clear error in the terminal instead of taking any Gmail action.

While MailBot is speaking in `voice` mode, you can press:

- `P` to pause or resume spoken output
- `S` to skip the rest of the current spoken response

## Example Flow

```powershell
mailbot unread --limit 5
mailbot cleanup --limit 10
mailbot review --trash-candidates
mailbot trash --ids 3
mailbot review
```

`cleanup` is recommendation only. No messages are moved or deleted during cleanup.

`trash` moves selected eligible messages to Gmail Trash only. It does not permanently delete them.

MailBot v1 has no send, draft, or reply functionality.

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
