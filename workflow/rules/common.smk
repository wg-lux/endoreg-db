def video_transcode_upstream_receipt(wildcards):
    job = WORKFLOW_CONFIG.video_transcodes[wildcards.job]
    if job.import_job is None:
        return []
    return [str(RECEIPT_DIRECTORY / f"video/{job.import_job}.json")]


def video_hls_upstream_receipt(wildcards):
    job = WORKFLOW_CONFIG.video_hls_materializations[wildcards.job]
    if job.transcode_job is not None:
        return [
            str(
                RECEIPT_DIRECTORY
                / f"video_transcode/{job.transcode_job}.json"
            )
        ]
    if job.import_job is not None:
        return [str(RECEIPT_DIRECTORY / f"video/{job.import_job}.json")]
    return []
