rule import_video:
    input:
        source=lambda wildcards: str(
            WORKFLOW_CONFIG.video_imports[wildcards.job].source
        ),
    output:
        receipt=str(RECEIPT_DIRECTORY / "video/{job}.json"),
    params:
        job=lambda wildcards: WORKFLOW_CONFIG.video_imports[
            wildcards.job
        ].model_dump(mode="json"),
        resources=WORKFLOW_CONFIG.resources.video.model_dump(mode="json"),
        django_settings_module=WORKFLOW_CONFIG.django_settings_module,
        batch_id=BATCH_ID,
        config_sha256=CONFIG_SHA256,
    log:
        stage=str(LOG_DIRECTORY / BATCH_ID / "video/{job}.json"),
    threads:
        WORKFLOW_CONFIG.resources.video.threads
    resources:
        mem_mb=WORKFLOW_CONFIG.resources.video.mem_mb,
        rust_workers=WORKFLOW_CONFIG.resources.video.rust_workers,
        gpu=WORKFLOW_CONFIG.resources.video.gpu,
        runtime=WORKFLOW_CONFIG.resources.video.runtime_minutes,
    script:
        "../scripts/run_video_import.py"
