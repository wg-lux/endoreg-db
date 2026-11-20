rule transcode_processed_video:
    input:
        video_transcode_upstream_receipt,
    output:
        receipt=str(RECEIPT_DIRECTORY / "video_transcode/{job}.json"),
    params:
        job=lambda wildcards: WORKFLOW_CONFIG.video_transcodes[
            wildcards.job
        ].model_dump(mode="json"),
        resources=WORKFLOW_CONFIG.resources.resolved_video_transcode.model_dump(
            mode="json"
        ),
        django_settings_module=WORKFLOW_CONFIG.django_settings_module,
        batch_id=BATCH_ID,
        config_sha256=CONFIG_SHA256,
    log:
        stage=str(LOG_DIRECTORY / BATCH_ID / "video_transcode/{job}.json"),
    threads:
        WORKFLOW_CONFIG.resources.resolved_video_transcode.threads
    resources:
        mem_mb=WORKFLOW_CONFIG.resources.resolved_video_transcode.mem_mb,
        rust_workers=WORKFLOW_CONFIG.resources.resolved_video_transcode.rust_workers,
        gpu=WORKFLOW_CONFIG.resources.resolved_video_transcode.gpu,
        runtime=WORKFLOW_CONFIG.resources.resolved_video_transcode.runtime_minutes,
    script:
        "../scripts/run_video_transcode.py"
