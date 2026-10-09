# Utility Projects

This repo holds a couple of small utilities I made to fix things that annoyed me or didn’t exist by default.

## PDF Inverter (Python)

I made this because I like doing all of my writing on my iPad in GoodNotes, and I prefer writing in dark mode on graph paper—it’s just really pretty. The problem is that dark graph paper is not exactly condusive to printing math problem sets. Black backgrounds mean a *lot* of ink...

So I wrote a Python script that **inverts the PDF**, flipping and flooring the colors so the dark backgrounds become white and the text stays readable. This way I can write in dark mode and still print everything without destroying my printer.

## Move to Selected Folder (macOS Workflow)

This one exists because on Windows there’s a simple right-click option called **“Move to Selected Folder”**, which I used constantly. macOS does not have this (woot go mac!)

So I made a quick macOS workflow that gives me the same feature: right-click, choose the folder, and move the file there instantly. Not much just got annoyed enough that I decided to make a script haha.

# WhisperKit Transcribe

This is a Python script that basically just lets you insert an audio file and it will output a transcription. I made this for the occasional business class that assigned me a 60-minute podcast as homework that I *really* did not want to listen to all of. So I would transcribe it, listen to a little bit of it, and then use Control-F to find and quote what I wanted to talk about.

## [VS Code SSH Bridge](vcode-bridge/)

I created this to let me open an SSH project back in my VS Code instance using the Remote SSH feature on my Mac. There wasn’t an easy way for me to just run `code .` inside a normal SSH session and have it open, so I made a quick Go bridge that wraps the `vcode` command, sends the remote path back through a Unix socket, and triggers VS Code to open it through Remote SSH.

## [Device Terminal Setup](device-terminal-setup/)

I kept rebuilding the same shell by hand every time I got a new machine, so this script does it for me: zsh, Oh My Zsh, starship, mise, uv, tmux and my Nerd Fonts. One curl line sets up a machine, the same line updates it, and `--uninstall` puts it back the way it was.
