# DiscSteward

DiscSteward is a Windows app that helps you identify the TV episodes in a
Blu-ray or DVD rip. It compares the disc's programmes with the MKV files made
by MakeMKV, then saves a readable mapping beside the ripped files. You can
review the results and move correctly identified episodes into a Plex TV
library with suitable filenames.

It is useful when MakeMKV names files something like `title_t00.mkv` and you
need to know which disc programme each file came from. DiscSteward can also
automate ripping and mapping, or help you review uncertain matches yourself.

DiscSteward does **not** assume that Programme 1 is Episode 1. It labels an
episode as verified only when there is suitable evidence, such as an exact
TheDiscDb match or a strong local subtitle match. Otherwise, it shows an
editable suggestion for you to check. It will not automatically rename an
unverified episode.

## Get started on Windows

This is the **0.3.0 source release**, not a standalone EXE. Download and extract
the complete project folder before launching it; Python must be installed.

1. Install Python 3.10 or newer, MakeMKV, and FFmpeg. See the
   [Windows setup guide](CLEAN-PC-SETUP.md) for where to get them and how to
   set up `ffprobe.exe`.
2. Keep this project folder together and double-click **Open DiscSteward.vbs**.
   The normal app opens without a Command Prompt window.
3. Select your Blu-ray or DVD drive (or an extracted-disc folder) and
   `ffprobe.exe`. For an existing rip, also select its MKV folder. DiscSteward
   remembers your choices.
4. Use **Make mapping** for files you have already ripped, or
   **Automated rip, map & preview** to let DiscSteward run MakeMKV. Review the
   report and any proposed rename before moving files.

The [Windows setup guide](CLEAN-PC-SETUP.md) also explains automatic disc
watching, manual matching, Plex renaming, and what each setting does. You do
not need to use the command line for normal operation.

## Help and safety

If the app will not open, double-click **DiscSteward-Diagnostics.cmd** in this
folder. It keeps the error visible so you can report it. For setup and usage
questions, check the [Windows setup guide](CLEAN-PC-SETUP.md) or
[open a GitHub issue](https://github.com/UpsideDownDisk/DiscSteward/issues).
Include the error message and whether the disc is a Blu-ray or DVD,
but do not upload copyrighted disc contents.

The project is read-only with respect to disc data. The optional Plex action
moves only selected MKV files after a rename preview; you can choose whether
safe renames require confirmation. It does not decrypt media or bypass copy
protection.

## Project files

- `Open DiscSteward.vbs` — normal double-click launcher (no console).
- `DiscSteward-Diagnostics.cmd` — diagnostic launcher that keeps errors visible.
- `discsteward-ui.py` — desktop application.
- `assets/discsteward-icon.png` — the DiscSteward toolbar/window icon.
- `src/bluray_map` — Blu-ray playlist and DVD IFO parsing, matching, reporting,
  DiscDb lookup, disc-title context, and labelled episode-order suggestions.
- `tests` — automated parser/classification tests.

The UI can also prepare a mounted disc, invoke `makemkvcon` with a user-selected
MakeMKV profile, match the completed rip, and optionally perform only safe Plex
moves. See the Windows guide for setup and safeguards.

## Local subtitle matching

Choose **Local subtitle folder** on the main page. After TheDiscDb, unresolved
files are compared with locally downloaded `.srt` subtitles, including those
inside `.zip` archives and subfolders. Use **Subtitle help** for
download guidance: prefer BDRip/BluRay subtitles for Blu-ray, DVDRip for DVD,
and keep explicit show/season/episode labels in the subtitle filenames.
No API account connection, key, download service, or additional Python package
is used. The existing FFmpeg `ffprobe.exe` installation reads embedded subtitles.

Only the middle two quarters (25–75%) of each video contribute to identification.
Full PGS, DVD bitmap, and common text subtitle tracks in the selected preferred
language are supported. **No preference** allows any supported full track;
forced-only, absent, unsupported, or sparsely captioned tracks remain unresolved.
The same **Preferred language** setting selects audio when choosing between
alternate ripped files. Existing saved language choices are retained.
The matcher compares subtitle event timing, not dialogue OCR. It checks fixed
offsets and common PAL/film speed conversions. Acceptance requires at least
30 events in each quarter, 75% overall coverage, 65% per quarter, 25 percentage
points above shifted-background overlap, and a 12-point lead over a different
episode. A second strong episode candidate blocks acceptance. Multiple versions
of the same episode do not compete with one another. Weak results go to manual
review. These thresholds are conservative and are not a calibrated probability.

The show/episode label comes from the matching subtitle filename. A wrongly
labelled reference can still give a wrong name; use trusted downloads covering
all candidate episodes. Existing MKV filenames and programme order are not used
to identify the video. Reports record the subtitle path (and ZIP member), hash,
coverage per quarter, time offset, speed conversion, and competing candidates.
The text report shows the selected reference and the most useful measurements.

The known disc series filters references: the SRT filename must contain that
series before its SxxEyy label (punctuation and case differences are ignored).
The existing-files window has a **Show / series** filter. It can be blank only
when the available subtitle references name a single series. A series name on
the ZIP alone does not substitute for a missing name on an SRT inside it.

Fresh subtitles are checked first. Only unresolved files fall back to
**previously used**. After matching the whole batch, successful reference SRTs
are moved into that subfolder, preserving relative directories. For references
inside ZIPs, the whole ZIP moves intact, leaving its other episodes available
through fallback on later runs. Only selected successful references move, not
weak matches or every version of an episode. Name collisions receive numbered
suffixes. Reports retain original and current locations; archive failures are
warnings and do not discard a match.

**Match existing / misnamed files…** opens a separate folder-and-run window.
It uses the main page's subtitle folder and scans MKVs recursively without a
disc. It saves `subtitle-episode-mapping.txt` and `.json` in the selected video
folder. **Rename and move…** previews Plex destinations in the main page's
library folder. **Rename in current folder…** previews episode filenames beside
each original video, without creating Show/Season folders. Both actions require
confirmation in this window, regardless of the automatic-rename option.
Matching does not rename videos; it does archive successfully used subtitles.

Completed video renames update paths in both mapping reports and are logged in
`DiscSteward-renames.jsonl`. Manual review displays an old-path → new-path popup
and a **View completed renames and moves** button. Completed files are not offered
again for manual assignment; remaining alternates are retained. Older reports
without a rename history are not retroactively marked as completed.

Automated rips are saved under `destination/disc name/`. Timestamped overall
progress and estimated remaining time are shown every five minutes; MakeMKV
messages are shown immediately and retained in `DiscSteward-MakeMKV.log`.
After a successful automated rip, DiscSteward checks whether the original disc
is still readable, ejects it when safe, and continues mapping from the saved
disc information. If the user has already ejected it, or the final check is
uncertain, mapping still continues and automatic eject is skipped. Failed rips
retain partial files and are not automatically mapped or renamed.
When **Watch for a newly loaded disc** is enabled, DiscSteward saves any
unresolved matches for later review and keeps watching for the next disc.
If automatic rename acceptance is off, rename confirmation is also deferred;
use **Open saved mapping** to review that disc later. Automatic acceptance still
renames safe matches without a confirmation dialog.
Mapping reports include copyable proposed Plex names for verified identities.
The `bluray-map` command and the `bluray_map` Python package remain compatible.
Existing settings are loaded on first use of DiscSteward.

**Manual match, rename and move** lets you review unmatched files or unverified
identities without retyping everything. Choose **Programme 1**, **Programme 2**
and so on from the dropdown, then tick one of its matching ripped files. Each
programme shows its report details and the audio languages of its matching files.
Your file choices and name edits stay saved while you switch programmes in that
window. When finished, preview all selected names and destinations together and
confirm the batch move. Unticked alternate files stay in the rip folder.

The show and season are filled from saved disc information when available;
older reports can use a rip folder name such as `Nikita Season 2 Disc 1` as a
labelled suggestion. The programme number is offered as an editable episode
suggestion. The three suggestion checkboxes let you restore suggested values
after editing. Suggestions are never automatic proof of episode identity.
An optional **First episode on this disc** field offsets the suggestions: enter
7 and click **Apply numbering** to suggest 7, 8, 9, etc. Blank means 1. Individual
episode edits and verified database identities are preserved. Reports record
**Episode numbering assumed from programme order**, and the starting number is
saved with that disc's mapping. These suggestions still require manual preview
and confirmation; they do not enable unattended verified renaming.
Use **Show other ripped files** to choose a file outside the programme's matches,
or **Open saved mapping…** to review a previous report without the disc.
Manual decisions are logged separately from automatic evidence. Closing the
manual window discards unconfirmed choices; confirmed moves remain in the log.

## Command-line use

The command-line tool is intended for development or batch investigation. It
requires Python 3.10+ and is run from this project folder:

```powershell
py -3 -m pip install -e .
discsteward D:\extracted-disc
discsteward D:\extracted-disc --json
discsteward D:\extracted-disc --csv --min-duration 15:00 --max-duration 30:00
discsteward D:\extracted-disc --ffprobe C:\ffmpeg\bin\ffprobe.exe
```

DiscSteward automatically detects `BDMV` for Blu-ray or `VIDEO_TS` for DVD.
Video lengths and timing differences use `HH:MM:SS.mmm` throughout the UI and
text reports (for example `01:02:03.456`). Non-zero differences below one
millisecond show as `<00:00:00.001`. JSON exports add readable `_display`
fields, and CSV adds a `duration` column; exact seconds remain available for
software and are unchanged for matching. Existing reports are updated when
you generate or save the mapping again.

Blu-ray MPLS parsing provides the clip order and 45 kHz in/out timestamps. DVD
IFO parsing follows the global title table through VTS chapter references to
programme/cell timing. Programme candidates
are based on duration and disc-native segment chains. Playlist and DVD title
numbers alone are not episode numbers; exact ordered chains are reported as
alternate-programme groups only when their ordered media ranges also agree.

DVD support covers simple sequential, single-angle titles. Multi-PGC, angle,
dynamic navigation and malformed title structures are reported as unsupported,
not guessed. If a DVD title cannot be resolved, automatic duration matching is
disabled for that disc: the missing title could have the same duration as a
parsed title. The parsed information and ripped files remain available for
manual review. Both disc types use a narrow 0.05-second timing allowance.

TheDiscDb lookup supports both formats. DVDs are checked by their IFO-based
Disc ID, then by TheDiscDb's legacy hash of all direct VIDEO_TS file sizes.
Only an exact identifier match is used: another region or edition is not a
verified substitute. DVD episode labels are joined by the global DVD title
number and checked against its runtime (database runtimes are whole seconds).
Conflicting database rows and ambiguous ripped-file timings are not guessed.
Auto mode saves the database result before ejection; JSON and text mapping
reports retain TheDiscDb attribution. An absent database entry or unsupported
DVD navigation still requires manual review, even if another edition is listed.

Automated ripping binds MakeMKV's drive index to the selected optical drive,
captures its source/program/profile settings, and rechecks the drive and disc
navigation fingerprint before ripping. It stops on a mismatch or when MakeMKV
cannot identify the drive letter. Extracted folders support **Make mapping**;
automated ripping requires a physical optical drive. Watch mode checks that
selected drive, including when it is empty.

DiscSteward checks the disc during preparation and just before MakeMKV starts.
It does not open the disc again while MakeMKV is ripping; MakeMKV reports copy
failures, including a disc removed during a rip. Failed runs retain partial
files and the log, skip automatic mapping/renaming, and can wait for another
disc. A stable insertion restarts preparation and uses a fresh output folder.
Turn off **Watch for a newly loaded disc** to stop waiting.

## Review regression checks

The no-database-match preparation record is safe to reuse after eject. Matching
menu-label and playlist numbers no longer count as verified identity, including
in older saved reports. DVD frame counts are decoded as BCD, and duplicate
grouping and duration ambiguity checks use the same ordered cut signature.

Regression tests use synthetic IFO/MPLS data, temporary dummy files and mocked
MakeMKV drive output. They do not establish compatibility with every authored
DVD or replace an end-to-end test with a real disc and optical drive.

## Development checks

The standard-library regression suite does not need extra Python packages:

```powershell
$env:PYTHONPATH = 'src'
py -3 -m unittest discover -s tests -v
py -3 -m compileall -q src discsteward-ui.py scripts
```

See [Contributing](CONTRIBUTING.md) for optional pytest and package-build checks,
and [Publishing](PUBLISHING.md) for creating a clean source ZIP. GitHub Actions
runs Windows checks on Python 3.10, 3.13 and 3.14 after upload.

## Licence

DiscSteward is available under the [MIT licence](LICENSE).
External programs and data retain their own terms; see
[Third-party software and data](THIRD_PARTY.md). No MakeMKV, FFmpeg, disc media
or downloaded subtitles are bundled with this project.
