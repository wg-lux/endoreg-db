# Video Format Reconciliation Services

The repository exposes a NixOS module for recurring managed-media video format
audits:

```nix
{
  imports = [
    inputs.endoreg-db.nixosModules.video-format-reconciliation
  ];

  services.endoreg_db.video_format = {
    enable = true;
    user = "endoreg";
    group = "endoreg";
    working_directory = "/opt/endoreg-db";
    python = "/opt/endoreg-db/venv/bin/python";
    environment_file = "/etc/endoreg-db/local-study-server.env";
    required_mounts = [ "/var/lib/lx-annotate" ];

    audit = {
      on_calendar = "daily";
      fail_on_non_compliant = false;
    };
  };
}
```

The audit unit runs:

```bash
python manage.py reconcile_video_formats --json --dry-run
```

It scans only managed Endoreg/LX-Annotate media roots by default and reports
videos that do not match the filewatcher-standard format:

- MP4 path suffix
- H.264 video
- `yuv420p` pixel format
- full-range `pc` color range
- AAC audio at 128k when repair is performed

Repair is intentionally disabled by default because it rewrites MP4 bytes. To
enable recurring repair, explicitly acknowledge in-place replacement:

```nix
services.endoreg_db.video_format.repair = {
  enable = true;
  allow_in_place = true;
  on_calendar = "Sun *-*-* 03:00:00";
};
```

Non-MP4 files are reported but skipped by in-place repair. They require explicit
re-import or path migration so the filesystem path, database references, hashes,
and provenance stay coherent.
