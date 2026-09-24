# External software and services

The MIT licence in this repository applies to DiscSteward. It does not change
the licences or terms of separate programs, services, downloaded subtitles or media.

## Software installed separately

- [Python](https://www.python.org/downloads/windows/) provides the interpreter
  and Tkinter interface. Python and its bundled components retain their own licences.
- [MakeMKV](https://www.makemkv.com/download/) creates rips and exposes the
  command-line automation used by DiscSteward. It is not included in this repository.
- [FFmpeg](https://ffmpeg.org/download.html) supplies `ffprobe.exe` for reading
  durations, audio tracks and subtitle timing. No FFmpeg binaries are bundled.

## Services and user-provided content

- [TheDiscDb](https://thediscdb.com/) provides disc/episode metadata. DiscSteward
  records attribution in its reports and sends a disc identifier for lookups.
- Users obtain subtitles separately, for example from OpenSubtitles. DiscSteward
  reads locally supplied files; no OpenSubtitles login or API key is included.
- Disc contents, video rips, downloaded subtitles and real-disc test extracts
  are not part of the source release. Repository tests create synthetic data.

If a future executable bundles third-party components, its distribution must
also include the notices required for those particular components.
