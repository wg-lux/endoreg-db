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
        django_settings_module=WORKFLOW_CONFIG.django_settings_module,
    threads:
        WORKFLOW_CONFIG.resources.video.threads
    resources:
        mem_mb=WORKFLOW_CONFIG.resources.video.mem_mb,
    script:
        "../scripts/run_video_import.py"
