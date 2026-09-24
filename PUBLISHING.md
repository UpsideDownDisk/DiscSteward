# Publishing DiscSteward

Repository: <https://github.com/UpsideDownDisk/DiscSteward>

## What to upload

Use the contents of the generated **DiscSteward-0.3.0-source.zip**, not the whole
working directory. It contains the application, launchers, icon, source, tests,
documentation, MIT licence and GitHub workflow files. Extract it first if uploading
through GitHub's **Add file → Upload files** page. Upload the contents of its
DiscSteward folder at the repository root, including `.github`, `.gitignore` and
`.gitattributes`. Do not put the ZIP itself into the source repository.

Local work, media, test output, notes, virtual environments and backup archives
are excluded. They are kept on the original PC.

Before uploading over an existing repository, check its current files and
history. Preserve changes already on GitHub; do not force-push a replacement.
This preparation does not publish a GitHub commit or release.

## Checks and releases

The **Windows checks** workflow runs automatically after a push or pull request.
It runs the tests on Python 3.10, 3.13 and 3.14, checks Python packaging, and
creates a clean source ZIP as a workflow artifact. A passing local run does not
mean those remote checks have already run.

Once the checks pass, use GitHub's **Releases → Draft a new release** to create
tag `v0.3.0`. Use the matching entry from `CHANGELOG.md` for release notes, and
attach the source ZIP and its `.sha256` checksum from the workflow artifact.
This is a source release requiring Python; do not describe it as an EXE release.

To rebuild the source ZIP locally:

```powershell
py -3 scripts/package_source.py
```

It writes into `dist/`, refuses to overwrite an existing release archive, and
lists only approved source files. Change the version in `pyproject.toml` and
add a changelog entry before preparing a later release.

## Executable distribution

A standalone Windows EXE can be added later. It needs a separate packaging and
clean-PC test, along with notices for any bundled runtime/components. MakeMKV
and FFmpeg remain separate installations unless their redistribution is reviewed.
