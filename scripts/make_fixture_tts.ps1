# Generates fixtures/sample-meeting.mp3 — a synthetic 2-speaker meeting via Windows TTS.
# Priya = Zira (female), Arjun = David (male). Content is crafted so later phases can
# assert on it: clear action items, a decision, and an "immediate next step".
$ErrorActionPreference = "Stop"
$env:Path = "$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:Path"  # prefer winget ffmpeg 9.x
$repo = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $env:TEMP "shruti_tts"
New-Item -ItemType Directory -Force $tmp | Out-Null
Remove-Item "$tmp\*" -Force -ErrorAction SilentlyContinue

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

$zira = "Microsoft Zira Desktop"; $david = "Microsoft David Desktop"
$lines = @(
    @($zira,  "Good morning everyone. Let us start the weekly launch readiness meeting."),
    @($david, "Thanks Priya. The propulsion test on Thursday was successful. All engines performed within limits."),
    @($zira,  "That is great news. What about the avionics integration?"),
    @($david, "Avionics is two days behind schedule. We need one more week of bench testing before we can sign off."),
    @($zira,  "Understood. Then the immediate priority is to finish the avionics bench test by next Friday."),
    @($david, "Agreed. I will also send the updated budget report to the finance team tomorrow morning."),
    @($zira,  "Perfect. So the action items are: Arjun sends the budget report tomorrow, and the avionics team completes bench testing by Friday."),
    @($david, "Correct. One more thing: we should book the vibration test facility for the first week of next month."),
    @($zira,  "Good point. I will book the facility today. Thank you everyone, meeting closed.")
)

$listFile = Join-Path $tmp "concat.txt"
$entries = @()
$i = 0
foreach ($line in $lines) {
    $voice, $text = $line
    $wav = Join-Path $tmp ("utt{0:d2}.wav" -f $i)
    $synth.SelectVoice($voice)
    $synth.SetOutputToWaveFile($wav, $fmt)
    $synth.Speak($text)
    $entries += "file '" + $wav.Replace("\", "/") + "'"
    $entries += "file '" + (Join-Path $tmp "silence.wav").Replace("\", "/") + "'"
    $i++
}
$synth.SetOutputToNull(); $synth.Dispose()

ffmpeg -hide_banner -loglevel error -y -f lavfi -i anullsrc=r=16000:cl=mono -t 0.7 (Join-Path $tmp "silence.wav")
Set-Content -Path $listFile -Value ($entries -join "`n") -Encoding ascii

$out = Join-Path $repo "fixtures\sample-meeting.mp3"
New-Item -ItemType Directory -Force (Join-Path $repo "fixtures") | Out-Null
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i $listFile -c:a libmp3lame -b:a 64k -ar 16000 -ac 1 $out
$dur = ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $out
"generated $out (${dur}s)"
