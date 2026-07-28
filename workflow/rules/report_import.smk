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
    threads:
        WORKFLOW_CONFIG.resources.report.threads
    resources:
        mem_mb=WORKFLOW_CONFIG.resources.report.mem_mb,
    script:
        "../scripts/run_report_import.py"
