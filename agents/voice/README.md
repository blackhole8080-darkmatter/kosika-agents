# Kosi's voice

Say "Hey Kosi", ask, and hear the answer.

    python agents/voice/kosi_voice.py --check
    python agents/voice/kosi_voice.py --engine ..\kn.exe
    python agents/voice/kosi_voice.py --engine ..\kn.exe --say "hey kosi what time is it"

- `main.kos` decides, in Kosika code and with no model, whether what was heard
  is for Kosi (the wake words, or a talk already going on), where it goes
  (Kosi answers at once; work to plan and check goes to Kosika AI; stop;
  goodbye), and how an answer is said: short sentences, no code or links
  read out.
- `kosi_voice.py` moves the sound: microphone, ears (faster-whisper on this
  computer, else Groq's free Whisper), Kosi or Kosika AI, mouth (Studio's
  voice: Kokoro on this computer first; else the voice built into Windows).

Free and offline on the laptop needs, once: `pip install faster-whisper sounddevice numpy`
(Kokoro for Studio's voice is optional; without it Windows' own voice speaks).
