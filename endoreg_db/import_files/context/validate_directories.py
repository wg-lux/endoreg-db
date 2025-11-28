from .import_context import ImportContext

def _validate_path(import_context: ImportContext, anonym_dir, processed_dir, target_dir) -> bool:

    # Track processed files to prevent duplicates
    try:
        # Ensure anonym_report directory exists before listing files
        anonym_report_dir = Path(ANONYM_VIDEO_DIR)
        if anonym_report_dir.exists():
            ImportContext.processed_files = set(str(anonym_report_dir / file) for file in os.listdir(ANONYM_VIDEO_DIR))
        else:
            logger.info(f"Creating anonym_reports directory: {anonym_report_dir}")
            anonym_report_dir.mkdir(parents=True, exist_ok=True)
            self.processed_files = set()
    except Exception as e:
        logger.warning(f"Failed to initialize processed files tracking: {e}")
        self.processed_files = set()
    if project_root:
        self.project_root = Path(project_root)
    else:
        self.project_root = Path(__file__).parent.parent.parent.parent
