rule materialize_video_hls:
    input:
        video_hls_upstream_receipt,
    output:
        receipt=str(RECEIPT_DIRECTORY / "video_hls/{job}.json"),
    params:
        job=lambda wildcards: WORKFLOW_CONFIG.video_hls_materializations[
            wildcards.job
        ].model_dump(mode="json"),
        resources=WORKFLOW_CONFIG.resources.resolved_video_hls.model_dump(
            mode="json"
        ),
        django_settings_module=WORKFLOW_CONFIG.django_settings_module,
        batch_id=BATCH_ID,
        config_sha256=CONFIG_SHA256,
    log:
        stage=str(LOG_DIRECTORY / BATCH_ID / "video_hls/{job}.json"),
    threads:
        WORKFLOW_CONFIG.resources.resolved_video_hls.threads
    resources:
        mem_mb=WORKFLOW_CONFIG.resources.resolved_video_hls.mem_mb,
        rust_workers=WORKFLOW_CONFIG.resources.resolved_video_hls.rust_workers,
        gpu=WORKFLOW_CONFIG.resources.resolved_video_hls.gpu,
        runtime=WORKFLOW_CONFIG.resources.resolved_video_hls.runtime_minutes,
    script:
        "../scripts/run_video_hls_materialization.py"
