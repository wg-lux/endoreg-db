rule import_report:
    input:
        source=lambda wildcards: str(
            WORKFLOW_CONFIG.report_imports[wildcards.job].source
        ),
    output:
        receipt=str(RECEIPT_DIRECTORY / "report/{job}.json"),
    params:
        job=lambda wildcards: WORKFLOW_CONFIG.report_imports[
            wildcards.job
        ].model_dump(mode="json"),
        django_settings_module=WORKFLOW_CONFIG.django_settings_module,
        batch_id=BATCH_ID,
        config_sha256=CONFIG_SHA256,
    log:
        stage=str(LOG_DIRECTORY / BATCH_ID / "report/{job}.json"),
    threads:
        WORKFLOW_CONFIG.resources.report.threads
    resources:
        mem_mb=WORKFLOW_CONFIG.resources.report.mem_mb,
        rust_workers=WORKFLOW_CONFIG.resources.report.rust_workers,
        gpu=WORKFLOW_CONFIG.resources.report.gpu,
        runtime=WORKFLOW_CONFIG.resources.report.runtime_minutes,
    script:
        "../scripts/run_report_import.py"
