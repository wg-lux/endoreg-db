
## 2026-07-09 HLS materialization hotfix attempt

- The production HLS worker wedged on video `4` for more than two hours while running FFmpeg with `-i pipe:0`.
- The worker consumed CPU but produced no temporary `seg_*.ts` files and no final `playlist.m3u8`, so this was not normal long-video behavior.
- The deployed code contained the seekable-input fallback, but the MP4 atom-position heuristic still allowed this video to use pipe input.
- The repo source was hotfixed so MP4-like inputs, detected either by file suffix or by an `ftyp` box in the decrypted prefix, always use the secure seekable temp-file path instead of `pipe:0`.
- Direct hotpatching of the installed wheel at `/var/endoreg-service-user/lx-annotate-wheel/.venv/lib/python3.12/site-packages/endoreg_db/services/hls_media.py` failed because the file is owned by `endoreg-service-user` and `admin` does not have passwordless sudo.
- The live worker must be restarted after the installed wheel is patched or after a new package containing the repo hotfix is deployed.
- Re-importing video `4` should not be the first response. The next operational step is to rerun HLS materialization with seekable input. Re-import is only justified if FFmpeg also fails when reading a seekable decrypted source file.

## 2026-07-09 root partition cleanup during HLS materialization incident

- The root filesystem was full before restarting the HLS worker: `/dev/dm-0` had roughly `31M` free on a `477G` btrfs root.
- The active HLS materialization was not materially filling the disk. `/var/lib/lx-annotate/data/storage/temp/hls_output` and `/var/lib/lx-annotate/data/storage/temp/hls_key_material` were both `0` bytes during the incident.
- The HLS worker was CPU-wedged on video `4` with FFmpeg still using `-i pipe:0`; it later hit Celery's `18000s` soft time limit and was immediately re-received, again using `pipe:0`.
- The biggest confirmed production storage hog that is HLS-related but not active HLS output is `/var/lib/lx-annotate/data/storage/streamable_videos`: `13` legacy progressive MP4 files totaling about `48.5G`. These are owned by `endoreg-service-user`, contain no `.m3u8` or `.ts` HLS files, and are not open by any process. Admin could not delete them without service-user privileges or sudo.
- Low-risk admin-owned cleanup freed space by removing generated caches, test runtime artifacts, editor logs, hidden Trash entries, old static build directories, and unreachable Nix store paths.
- Nix garbage collection removed stale GC roots, including old `/tmp/nh-*` roots and old LX-Annotate package generations, and reported `2403 store paths deleted, 5797.22 MiB freed`.
- Remaining privileged cleanup candidates: service-user caches under `/var/endoreg-service-user/.cache`, stale service-user `.devenv` directories, stale wheel frame extraction data under `/var/endoreg-service-user/lx-annotate-wheel/data/frames`, old journals under `/var/log/journal`, and legacy progressive MP4s under `/var/lib/lx-annotate/data/storage/streamable_videos`.
