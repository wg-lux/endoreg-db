{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.endoreg_db.video_format;

  root_args = lib.concatMapStringsSep " " (
    root: "--root ${lib.escapeShellArg root}"
  ) cfg.roots;

  extension_args = lib.concatMapStringsSep " " (
    extension: "--extension ${lib.escapeShellArg extension}"
  ) cfg.extensions;

  default_root_args =
    if cfg.include_default_roots then
      "--include-default-roots"
    else
      "--no-default-roots";

  max_files_args = lib.optionalString (
    cfg.max_files != null
  ) "--max-files ${toString cfg.max_files}";

  common_args = lib.concatStringsSep " " (
    [
      (lib.escapeShellArg cfg.manage_py)
      "reconcile_video_formats"
      "--json"
      default_root_args
      root_args
      extension_args
      "--min-free-bytes ${toString cfg.min_free_bytes}"
      max_files_args
    ]
    ++ lib.optional cfg.force_cpu "--force-cpu"
  );

  run_command = extra_args: ''
    exec ${pkgs.util-linux}/bin/ionice -c ${toString cfg.io_scheduling_class} -n ${toString cfg.io_scheduling_priority} \
      ${pkgs.coreutils}/bin/nice -n ${toString cfg.nice} \
      ${lib.escapeShellArg cfg.python} ${common_args} ${extra_args}
  '';

  base_service_config = {
    Type = "oneshot";
    User = cfg.user;
    Group = cfg.group;
    WorkingDirectory = cfg.working_directory;
    TimeoutStartSec = cfg.timeout_start_sec;
    Nice = cfg.nice;
    IOSchedulingClass = cfg.io_scheduling_class;
    IOSchedulingPriority = cfg.io_scheduling_priority;
    NoNewPrivileges = true;
    PrivateTmp = true;
  } // lib.optionalAttrs (cfg.environment_file != null) {
    EnvironmentFile = cfg.environment_file;
  };
in
{
  options.services.endoreg_db.video_format = {
    enable = lib.mkEnableOption "recurring Endoreg video format audits";

    user = lib.mkOption {
      type = lib.types.str;
      default = "endoreg";
      description = "User that runs the video format audit and repair commands.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "endoreg";
      description = "Group that runs the video format audit and repair commands.";
    };

    working_directory = lib.mkOption {
      type = lib.types.str;
      default = "/opt/endoreg-db";
      description = "Working directory containing manage.py.";
    };

    manage_py = lib.mkOption {
      type = lib.types.str;
      default = "${cfg.working_directory}/manage.py";
      description = "Path to the Endoreg Django manage.py entrypoint.";
    };

    python = lib.mkOption {
      type = lib.types.str;
      default = "${cfg.working_directory}/venv/bin/python";
      description = "Python executable used to run manage.py.";
    };

    django_settings_module = lib.mkOption {
      type = lib.types.str;
      default = "endoreg_db.config.settings.prod";
      description = "Django settings module for the reconciliation commands.";
    };

    environment_file = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = "/etc/endoreg-db/local-study-server.env";
      description = "Optional EnvironmentFile consumed by the systemd units.";
    };

    include_default_roots = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Scan the application default managed video roots.";
    };

    roots = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Additional managed media roots to scan.";
    };

    extensions = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        ".avi"
        ".m4v"
        ".mkv"
        ".mov"
        ".mp4"
        ".mpeg"
        ".mpg"
        ".webm"
      ];
      description = "Video filename extensions included in recurring scans.";
    };

    min_free_bytes = lib.mkOption {
      type = lib.types.int;
      default = 50 * 1024 * 1024 * 1024;
      description = "Minimum free bytes required before a repair transcode starts.";
    };

    max_files = lib.mkOption {
      type = lib.types.nullOr lib.types.int;
      default = null;
      description = "Optional cap on files checked per run.";
    };

    force_cpu = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Force CPU H.264 encoding instead of automatic NVENC selection.";
    };

    nice = lib.mkOption {
      type = lib.types.int;
      default = 10;
      description = "CPU scheduling niceness for recurring video format jobs.";
    };

    io_scheduling_class = lib.mkOption {
      type = lib.types.int;
      default = 2;
      description = "ionice scheduling class for recurring video format jobs.";
    };

    io_scheduling_priority = lib.mkOption {
      type = lib.types.int;
      default = 7;
      description = "ionice scheduling priority for recurring video format jobs.";
    };

    timeout_start_sec = lib.mkOption {
      type = lib.types.str;
      default = "24h";
      description = "systemd TimeoutStartSec for audit and repair oneshot jobs.";
    };

    required_mounts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "/var/lib/lx-annotate" ];
      description = "Mount paths that must be available before jobs run.";
    };

    audit = {
      on_calendar = lib.mkOption {
        type = lib.types.str;
        default = "daily";
        description = "systemd calendar expression for video format audits.";
      };

      persistent = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Whether missed audit timer runs should execute after boot.";
      };

      fail_on_non_compliant = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Make the audit unit fail when non-compliant videos are found.";
      };
    };

    repair = {
      enable = lib.mkEnableOption "recurring in-place video format repair";

      allow_in_place = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Required acknowledgement for in-place MP4 replacement.";
      };

      on_calendar = lib.mkOption {
        type = lib.types.str;
        default = "Sun *-*-* 03:00:00";
        description = "systemd calendar expression for video format repairs.";
      };

      persistent = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Whether missed repair timer runs should execute after boot.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.include_default_roots || cfg.roots != [ ];
        message = "services.endoreg_db.video_format needs default roots or explicit roots.";
      }
      {
        assertion = (!cfg.repair.enable) || cfg.repair.allow_in_place;
        message = ''
          services.endoreg_db.video_format.repair.enable requires
          repair.allow_in_place = true because repair rewrites MP4 files after
          ffmpeg verification.
        '';
      }
    ];

    systemd.services.endoreg-video-format-audit = {
      description = "Endoreg managed video format audit";
      wants = [ "network-online.target" ];
      after = [
        "network-online.target"
        "postgresql.service"
      ];
      unitConfig.RequiresMountsFor = cfg.required_mounts;
      path = [ pkgs.ffmpeg-headless ];
      environment = {
        DJANGO_SETTINGS_MODULE = cfg.django_settings_module;
        ENDOREG_VIDEO_FORMAT_MIN_FREE_BYTES = toString cfg.min_free_bytes;
      };
      serviceConfig = base_service_config;
      script = run_command (
        "--dry-run"
        + lib.optionalString cfg.audit.fail_on_non_compliant " --fail-on-non-compliant"
      );
    };

    systemd.timers.endoreg-video-format-audit = {
      description = "Run Endoreg managed video format audit";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.audit.on_calendar;
        Persistent = cfg.audit.persistent;
        Unit = "endoreg-video-format-audit.service";
      };
    };

    systemd.services.endoreg-video-format-repair = lib.mkIf cfg.repair.enable {
      description = "Endoreg managed video format repair";
      wants = [ "network-online.target" ];
      after = [
        "network-online.target"
        "postgresql.service"
      ];
      unitConfig.RequiresMountsFor = cfg.required_mounts;
      path = [ pkgs.ffmpeg-headless ];
      environment = {
        DJANGO_SETTINGS_MODULE = cfg.django_settings_module;
        ENDOREG_VIDEO_FORMAT_MIN_FREE_BYTES = toString cfg.min_free_bytes;
      };
      serviceConfig = base_service_config;
      script = run_command "--repair --in-place";
    };

    systemd.timers.endoreg-video-format-repair = lib.mkIf cfg.repair.enable {
      description = "Run Endoreg managed video format repair";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.repair.on_calendar;
        Persistent = cfg.repair.persistent;
        Unit = "endoreg-video-format-repair.service";
      };
    };
  };
}
