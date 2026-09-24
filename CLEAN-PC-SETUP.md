# DiscSteward — Windows guide

## What this tool does

DiscSteward compares Blu-ray playlists or DVD titles with the MKV files made by
MakeMKV. It automatically detects the disc type. It creates a report showing
which ripped file belongs to each detected programme. Where the disc provides
reliable episode information, it can also move a selected file into a
Plex-compatible TV library name.

It does not decrypt discs, alter the disc folder, or delete files.

## Before you begin

Install these programs on the Windows PC:

1. **[Python 3.10 or newer](https://www.python.org/downloads/windows/)**. During installation, select **Add Python to PATH**.
   Python includes the small graphical-interface component used by this source
   version of the mapper.
2. **[MakeMKV](https://www.makemkv.com/download/)** to create the MKV rips.
3. **[FFmpeg](https://ffmpeg.org/download.html)** (choose a Windows build). The mapper uses `ffprobe.exe` from FFmpeg's `bin` folder to read
   MKV duration, audio tracks and embedded subtitle timing; it does not re-encode video.

Keep these items together when copying the tool to another PC:

- `Open DiscSteward.vbs`
- `discsteward-ui.py`
- the complete `src` folder
- the `assets` folder (contains the application icon)

## Start the mapper

Double-click **Open DiscSteward.vbs**. This opens the application
without a Command Prompt window. `DiscSteward-Diagnostics.cmd`
is only a troubleshooting launcher: use it if the app does not open, because
it keeps any Python error on screen.

The app remembers the folders and program locations you select. Each **Browse**
button reopens in its currently selected location.

The main page puts action buttons at the top, followed by disc, subtitle and
library folders, naming/audio choices, then rip-folder and program settings.
The MakeMKV drive number is detected internally and no longer shown as a field.
On smaller screens, scroll the controls with the right-hand scrollbar.

Existing Blu-ray Episode Mapper settings are imported automatically. DiscSteward
saves settings under `%APPDATA%\DiscSteward`.

While making a mapping, the FFmpeg checks run silently in the background; they
do not open a Command Prompt window for each MKV file.

## Map a disc

1. Put a Blu-ray or DVD in the drive, or select the extracted-disc folder.
   Choose the folder containing `BDMV` for Blu-ray or `VIDEO_TS` for DVD (or a
   folder above it). DiscSteward detects which type it is.
2. Choose **Rip folder (auto: parent of disc folders)**. For manual mapping,
   select the folder containing this disc's existing `.mkv` files.
3. Choose `ffprobe.exe` from the FFmpeg `bin` folder. This only needs doing once
   on each PC.
4. Click **Open MakeMKV**, rip the disc, then return to the mapper.
5. Click **Make mapping**.

## Automated rip, map, and rename

For the one-button workflow, fill in these additional fields once:

1. **MakeMKV automation program** — browse to `makemkvcon64.exe` (or
   `makemkvcon.exe`) in the MakeMKV installation folder.
2. **Saved MakeMKV profile** — optional. Browse to a profile when you want to
   override MakeMKV's usual settings. Leave it blank to use the normal settings
   saved in MakeMKV itself.
3. **Disc source** — select the optical drive, for example `F:\`.
   The MakeMKV drive number is detected automatically from that drive letter;
   you no longer need to enter it, even with multiple drives. The tool checks
   the drive and disc again before ripping and stops if either has changed.
   If MakeMKV does not report device paths, update MakeMKV; the tool will not
   guess a drive index. Extracted disc folders use **Make mapping**, not auto rip.

The **Rip folder (auto: parent of disc folders)** is optional for automated ripping.
When blank, the mapper reads MakeMKV's configured **Default Destination** and
uses that as the parent folder. If MakeMKV has no configured default destination,
the mapper asks for a folder after a disc is detected. DiscSteward automatically
creates a disc-name subfolder, for example:

`D:\Rips\Nikita Season 2 Disc 1\Nikita Season 2 Disc 1_t00.mkv`

The disc name comes from the disc's metadata; unavailable names use the source
folder name or `Unnamed disc`. Invalid Windows filename characters are removed.
If the disc folder already exists, a fresh numbered folder such as
`Nikita Season 2 Disc 1 (2)` is created. Existing rips are not mixed into a new run.
The actual destination is shown on screen. Reports and logs go inside that folder.

For DVDs, DiscSteward uses MakeMKV's disc name first. If MakeMKV does not provide
a useful name, it uses the Windows disc-volume label. If neither is available,
the safe fallback is timestamped, for example `2026-09-20_14-35-10 - DVD`.
It does not use a drive-letter folder such as `DVD Disc F`.

Click **Automated rip, map & preview**. The mapper first prepares and saves
`prepared-disc.json` beside the rips. It then runs MakeMKV with the saved
profile, waits for a successful rip, ejects the original disc when safe, and maps
the new files using the prepared disc information. The prepared evidence
is retained if you eject the disc after the rip finishes.

With disc watching off, the app opens the rename preview or manual review when
needed. With **Watch for a newly loaded disc** enabled, it saves pending reviews
instead of interrupting the next disc: use **Open saved mapping** later.
**Automatically accept safe Plex renames** still applies safe renames in watch mode.

Automatic eject runs only after a successful rip. If the drive cannot eject,
the tool reports the error and continues mapping. You can then eject it manually.

During ripping, DiscSteward shows a timestamp, overall percentage, elapsed time
and estimated time remaining. Progress updates appear at the start, on the first
progress report and every five minutes afterwards. Remaining time is an estimate
based on overall progress, and initially shows `calculating`. If progress stops
arriving, the estimate is marked unavailable. Completion is shown immediately.

MakeMKV messages, including errors, appear immediately in the output area. Full
output and timestamped updates are saved to `DiscSteward-MakeMKV.log` beside the
ripped files. A failed MakeMKV exit stops automatic mapping and renaming.

### Watch for new disc

Select **Watch for a newly loaded disc** to watch the selected source drive.
It is normal for the chosen drive (for example `F:\`) to be empty when this is
enabled. The mapper waits until a disc is inserted before it checks for `BDMV`
or `VIDEO_TS`.
When it detects a different disc, it starts the automated workflow. Turn this
option off before changing discs if you do not want the next disc processed.

If you remove or replace a disc during preparation or ripping, the tool stops
that run and waits for the next readable disc. It starts preparation again when
the disc is ready, including when a different disc has been inserted. Partial
rips and logs are kept in the interrupted run's folder and are not mapped or
renamed automatically. The next run uses a fresh folder. Recovery enables disc
watch if necessary; turn the watch option off to stop waiting.

### Automatically accept safe Plex renames

Select **Automatically accept safe Plex renames** only after confirming your
folder, naming, and audio settings. It skips the confirmation prompt but still
refuses to move a file without a verified episode number, the selected audio,
or a free destination path. It never overwrites a file.

The mapper writes these files directly beside the ripped MKVs:

- `disc-episode-mapping.txt` — easy-to-read report.
- `disc-episode-mapping.json` — detailed machine-readable report.

These report filenames are retained for compatibility. Their heading is now
DiscSteward. Each matched programme with a verified identity includes a
**Proposed filename (copy this)** and a library-relative path. The on-screen
report is selectable for copying, and JSON contains `proposed_filename`,
`proposed_relative_path` and, when a library is selected, `proposed_destination`.
When episode identity is unverified, no episode filename is invented. Alternate
rips share one proposed name; only one should be renamed to it.

The report records the Blu-ray playlist or DVD title, its disc-native segment
chain, matched MKV files, and the source of any series/season/episode
identification. The matcher uses the main video track's duration when available.
Audio sometimes lasts longer than the video; measuring the whole file in that
case can incorrectly reject a match.
If video timing is unavailable, it falls back to whole-file duration. Both
methods retain the strict 0.05-second allowance. Reports identify the timing
method and the measured difference; JSON also retains the whole-file duration.

If a file's duration fits multiple different Blu-ray playlists or DVD title
chains, the matcher leaves it unmatched and records a warning. Alternate
programmes with the same segment chain can still share matching files. Matching
a file to a programme does not by itself verify its season or episode number.

When more than one MKV has the same programme duration, the text report also
compares their audio tracks. It shows the language/default-track differences,
or records that the audio inventory is the same.

MakeMKV often rips longer bonus material as well as episodes. The mapper groups
the main cluster of similarly timed programmes as likely episodes and reports
longer duration outliers under **Extras / non-episode programmes**. Extras are
matched to their ripped files for reference, but are not assigned episode
numbers and are never included in Plex rename proposals.

## How episode numbers are decided

The mapper first checks the disc's content hash against **TheDiscDb**. A match
can provide series, season, episode, and title information; the report labels
the source as `TheDiscDb`.

If there is no database match, the tool next tries your local subtitle folder,
when one is selected. Strong matches in both middle quarters can supply an
episode identity and a proposed Plex name. Reports label the source **Local
subtitle timing match** and record the exact subtitle used. Its filename supplies
the episode label; this is not independent catalogue verification of that label.

If neither source identifies the episode, programme order supplies editable episode
suggestions. Reports label these **Episode numbering assumed from programme
order**. Matching numbers in Blu-ray menu labels and playlist filenames are
not treated as a verified episode link, including in older saved reports.
Disc-title metadata can still fill in the suggested show and season.

If neither source identifies the information reliably, the report says **episode number
NOT VERIFIED**. Playlist order, disc number, and file name are never used as
proof that a file is Episode 1, Episode 19, or any other episode. You can still
use the mapping report to inspect durations and playlist/file relationships.

### Getting and using local subtitles

1. In your browser, download subtitles in your preferred language from OpenSubtitles.com or
   OpenSubtitles.org for the show and season. Download subtitles for all possible
   episodes; several versions of each are fine.
2. Prefer **BDRip / BluRay** releases for a Blu-ray rip, or **DVDRip** releases
   for a DVD rip. TV and web releases can have different cuts or timing.
3. Put the downloaded `.srt` or `.zip` files in a folder. ZIPs do not need to be
   unpacked. Subfolders are searched too. Keep filenames containing the show,
   season and episode, for example `Nikita.S02E01.BDRip.srt`.
4. Select **Local subtitle folder** on the main page. The app remembers it.
   **Subtitle help** shows this guidance inside the app.
5. When ripping, keep the full subtitle track in your **preferred language** selected in MakeMKV.
   Forced subtitles alone usually contain too few captions for identification.

The matching uses the middle 50% of the video (25% through 75%), avoiding the
opening and ending quarters. It compares subtitle timing patterns and requires
strong agreement in both middle quarters. It can allow a timing offset and
common DVD speed differences. Poor matches, conflicting episode labels and
missing subtitle tracks are reported for manual review. There is no automatic
online download or API key to configure. No extra software beyond the existing
FFmpeg installation is required.

Subtitles for another series, or SRT filenames without a series name, are
ignored when filtering for the disc's show. The name must be in the SRT filename
itself, including inside ZIPs. Dot/space and case differences are accepted.

The tool checks fresh subtitles first. If they do not produce a reliable match,
it checks **previously used**. Successfully used subtitles move into this
subfolder after the batch finishes matching. For a ZIP reference, the whole ZIP
moves intact, so its other episodes remain available on later runs. Existing
files are not overwritten; a numbered suffix resolves name collisions. Reports
show where each used reference is now stored.

### Correct files that already have the wrong names

Choose the subtitle folder on the main page, then click **Match existing /
misnamed files…**. In the separate window, choose the video folder and click
**Run matching**. Set **Show / series** to filter a mixed subtitle collection;
leave it blank only when the subtitles contain one show. The disc is not needed,
and the current video names are ignored
when identifying episodes. Subfolders are included.

The results are saved as `subtitle-episode-mapping.txt` and
`subtitle-episode-mapping.json` in the selected video folder. Read the results,
then choose an action in the same window:

- **Rename and move…** — rename selected matches and put them in Show/Season
  folders inside the Plex library selected on the main page.
- **Rename in current folder…** — give selected matches their episode names
  while keeping each file in its existing folder, including any subfolders.

Both actions show a preview and require confirmation. Existing destination files
are never replaced. Matching itself does not rename videos; it does move used
subtitle references into **previously used**.

After a rename, both reports are updated with actual locations and completed
actions. A popup shows each original name and its new location. If manual review
opens for the remaining files, it also shows completed moves, with a
**View completed renames and moves** button to reopen the list. Renamed files are
excluded from manual file choices; unused alternate copies remain available.
These records apply to new runs; older reports have no history of earlier
automatic moves.

DVD parsing follows the disc's global titles, chapter references and programme
cells. It supports simple sequential single-angle titles. Unsupported angles,
multi-PGC playback, dynamic navigation or malformed tables produce warnings.
If some DVD titles cannot be resolved, automatic timing matches are disabled
for that disc, because those titles might share another title's duration. Use
manual review in that case; the tool does not guess around the missing evidence.

## Rename for Plex

Only use this after reviewing the mapping report.

1. Choose the root folder of the Plex TV library.
2. Select **Plex TV Series** as the naming convention.
3. Select the **Preferred language** for both audio and subtitle matching. English is selected by
   default; choose **No preference** if language should not affect the choice.
4. Click **Preview and rename** and read the proposed moves.
5. Confirm only if the preview is correct.

The Plex destination format is:

`Show Name\Season 01\Show Name - s01e01 - Episode Title.mkv`

The tool renames by moving the selected MKV into that Plex folder. It will not
overwrite an existing destination file.

### When two files match one episode

Some discs have alternate versions with the same duration. For each verified
episode, the mapper selects a file with the chosen language, prefers one where
that language is the default audio track, and uses the larger file as a final
tie-breaker. The preview explains its choice. Other matching files remain in
the original rip folder; nothing is deleted.

If no matching file has the selected audio language, that episode is not moved.
Choose another language (or **No preference**) and preview again.

## Manual matches and names

When automatic matching or episode identification is incomplete, click
**Manual match, rename and move**. After an automated run with unresolved
episodes, this window opens automatically unless disc watching is enabled.
You can also click **Open saved
mapping…** and choose `disc-episode-mapping.json` from a previous disc folder;
the disc does not need to remain inserted.

1. Choose **Programme 1**, **Programme 2**, etc. from the dropdown. Its report
   details appear above its matching files, with audio languages below them.
2. Tick one ripped file. Ticking another alternate replaces the first tick.
   Use **Show other ripped files for a manual choice** if needed.
3. Check the suggested show, season and episode. Edit any field if needed;
   ticking its suggestion box again restores the suggested value. Programme
   numbers are manual episode suggestions and do not verify episode identity.
   Older reports can use an explicit rip folder label for show/season suggestions.
   **First episode on this disc** is optional: leave it blank for suggestions
   starting at 1, or enter (for example) 7 and click **Apply numbering** for
   7, 8, 9, etc. Individual episode edits and verified database numbers are
   not overwritten. The starting number is saved in this disc's mapping, not
   assumed for the next disc. Preview also applies a typed starting number.
4. Choose the next programme from the dropdown or click **Next programme →**.
   Each programme keeps its ticked file and edits while this window stays open.
5. Check the destination folder. Plex names create Show/Season subfolders;
   a custom filename is placed directly inside the chosen destination.
6. Click **Preview selected names and destinations** to see the whole batch,
   then **Rename and move** in that preview to apply the selected changes.

**Read selected file details** gets video/audio details for older reports;
**Play selected file** opens your Windows video player. Unticked files stay in
the rip folder. Closing the window discards unconfirmed choices.

Manual moves always need your explicit confirmation, even when automatic rename
is enabled. Existing destination files are never overwritten. The decision and
original/new paths are appended to `DiscSteward-manual-decisions.jsonl` beside
the original rip report. These are labelled manual user assignments; automatic
evidence stays separate. The mapping reports retain the starting-number setting
and labelled order suggestions when you close the review. Successfully moved files disappear from the manual
review list, and alternate copies remain for you to review. Watch mode waits
while the manual review window is open.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| App does not open | Double-click `DiscSteward-Diagnostics.cmd` and read the error. Install Python 3.10+ if it says Python is missing. |
| `ffprobe not found` | Install/extract FFmpeg and browse to `ffprobe.exe` in its `bin` folder. |
| `No usable BDMV directory` or `No usable DVD VIDEO_TS directory` | Select the disc root, its `BDMV` folder for Blu-ray, or its `VIDEO_TS` folder for DVD; do not select the folder that merely contains MKVs. |
| `episode number NOT VERIFIED` | The disc/database has not provided proof of the episode number. Do not use automated rename for that item. |
| No safe rename candidates | Make a mapping first, select a Plex library folder, and ensure the episode numbers are verified. |
| No matching file has English audio | Select the correct preferred language or **No preference**, then preview again. |
| Automation program/profile not found | Browse to `makemkvcon64.exe` and the saved MakeMKV profile before starting automated ripping. |
