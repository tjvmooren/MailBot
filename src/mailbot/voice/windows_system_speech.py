from __future__ import annotations

import base64
import subprocess

from .base import SpeechToTextError, SpeechToTextProvider, TextToSpeechError, TextToSpeechProvider


class WindowsSpeechToTextProvider(SpeechToTextProvider):
    def __init__(
        self,
        *,
        max_duration_seconds: int = 8,
        initial_silence_seconds: int = 5,
        babble_timeout_seconds: int = 2,
        end_silence_seconds: int = 1,
    ) -> None:
        self.max_duration_seconds = max_duration_seconds
        self.initial_silence_seconds = initial_silence_seconds
        self.babble_timeout_seconds = babble_timeout_seconds
        self.end_silence_seconds = end_silence_seconds

    def transcribe_once(self) -> str:
        script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$recognizer.SetInputToDefaultAudioDevice()
$recognizer.InitialSilenceTimeout = [TimeSpan]::FromSeconds({self.initial_silence_seconds})
$recognizer.BabbleTimeout = [TimeSpan]::FromSeconds({self.babble_timeout_seconds})
$recognizer.EndSilenceTimeout = [TimeSpan]::FromSeconds({self.end_silence_seconds})
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$recognizer.LoadGrammar($grammar)
$result = $recognizer.Recognize([TimeSpan]::FromSeconds({self.max_duration_seconds}))
if ($null -eq $result -or [string]::IsNullOrWhiteSpace($result.Text)) {{
    exit 3
}}
Write-Output $result.Text
""".strip()
        completed = _run_powershell_script(script)
        if completed.returncode == 0:
            recognized = completed.stdout.strip()
            if recognized:
                return recognized
        if completed.returncode == 3:
            raise SpeechToTextError(
                "No speech was recognized. Check your microphone, speak clearly, and try again."
            )
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise SpeechToTextError(
            "Windows speech recognition is unavailable. "
            f"Details: {stderr or 'unknown error'}"
        )


class WindowsTextToSpeechProvider(TextToSpeechProvider):
    def speak(self, text: str) -> None:
        if not text.strip():
            return
        script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$text = @'
{text}
'@
$speaker.Speak($text)
""".strip()
        completed = _run_powershell_script(script)
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise TextToSpeechError(
                "Windows text-to-speech is unavailable. "
                f"Details: {stderr or 'unknown error'}"
            )


def _run_powershell_script(script: str) -> subprocess.CompletedProcess[str]:
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-EncodedCommand",
            encoded_script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
